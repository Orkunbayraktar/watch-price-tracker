"""Persistence helpers for storing normalized scraper output."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from database.db import db
from database.models import Listing, PriceHistory, Product, Seller
from scrapers.models import ScrapedProductData


logger = logging.getLogger(__name__)


class PersistenceError(RuntimeError):
	"""Raised when a database persistence transaction fails."""


@dataclass(slots=True)
class PersistenceResult:
	"""Return value for a successful scrape persistence transaction."""

	product: Product
	seller: Seller | None
	listing: Listing
	price_history: PriceHistory


def save_scraped_product(data: ScrapedProductData) -> PersistenceResult | None:
	"""Persist valid scraped product data atomically.

	Validation failures return None. Database failures are rolled back and raised
	as PersistenceError so callers can decide how to surface the failure.
	"""
	missing_fields = _get_missing_critical_fields(data)
	if missing_fields:
		logger.warning(
			"Skipping persistence because critical scraped fields are missing: %s",
			", ".join(missing_fields),
		)
		return None

	session = db.session
	try:
		product = _find_or_create_product(data)
		seller = _find_or_create_seller(data)
		session.flush()

		listing = _find_or_create_listing(data, product, seller)
		_update_listing(listing, data, product, seller)

		price_history = PriceHistory(
			listing=listing,
			price=data.current_price,
			old_price=data.old_price,
			discount_percentage=data.discount_percentage,
			recorded_at=data.scraped_at,
		)
		session.add(price_history)
		session.flush()
		session.commit()

		logger.info("Persisted scraped product data for %s", data.product_url)
		return PersistenceResult(
			product=product,
			seller=seller,
			listing=listing,
			price_history=price_history,
		)
	except SQLAlchemyError as error:
		session.rollback()
		logger.exception("Database persistence failed for %s", data.product_url)
		raise PersistenceError(f"Failed to persist scraped product data for {data.product_url}") from error


def _find_or_create_product(data: ScrapedProductData) -> Product:
	product = _find_product(data)
	if product is not None:
		product.name = data.product_name.strip()
		if product.brand is None and data.brand is not None:
			product.brand = data.brand.strip()
		if product.model is None and data.model is not None:
			product.model = data.model.strip()
		return product

	product = Product(
		brand=_clean_display_value(data.brand),
		model=_clean_display_value(data.model),
		name=data.product_name.strip(),
	)
	db.session.add(product)
	return product


def _find_product(data: ScrapedProductData) -> Product | None:
	normalized_name = _normalize_lookup_value(data.product_name)
	normalized_brand = _normalize_lookup_value(data.brand)
	normalized_model = _normalize_lookup_value(data.model)
	query = Product.query

	if normalized_brand and normalized_model:
		return (
			query.filter(
				func.lower(func.trim(Product.brand)) == normalized_brand,
				func.lower(func.trim(Product.model)) == normalized_model,
			)
			.order_by(Product.id)
			.first()
		)

	if normalized_brand and normalized_name:
		# This fallback intentionally uses exact normalized brand + name matching.
		# It is conservative and can be replaced by a richer product matcher later.
		return (
			query.filter(
				func.lower(func.trim(Product.brand)) == normalized_brand,
				func.lower(func.trim(Product.name)) == normalized_name,
			)
			.order_by(Product.id)
			.first()
		)

	if normalized_name:
		return (
			query.filter(func.lower(func.trim(Product.name)) == normalized_name)
			.order_by(Product.id)
			.first()
		)

	return None


def _find_or_create_seller(data: ScrapedProductData) -> Seller | None:
	platform = data.platform.strip()
	external_seller_id = _clean_display_value(getattr(data, "external_seller_id", None))
	if external_seller_id:
		seller = (
			Seller.query.filter_by(platform=platform, external_seller_id=external_seller_id)
			.order_by(Seller.id)
			.first()
		)
		if seller is None:
			seller_name = _clean_display_value(data.seller_name)
			if seller_name is None:
				return None
			seller = Seller(
				platform=platform,
				external_seller_id=external_seller_id,
				name=seller_name,
				rating=data.seller_rating,
			)
			db.session.add(seller)
			return seller

		if data.seller_rating is not None:
			seller.rating = data.seller_rating
		return seller

	normalized_seller_name = _normalize_lookup_value(data.seller_name)
	if normalized_seller_name is None:
		return None

	seller = (
		Seller.query.filter(
			Seller.platform == platform,
			func.lower(func.trim(Seller.name)) == normalized_seller_name,
		)
		.order_by(Seller.id)
		.first()
	)
	if seller is not None:
		if data.seller_rating is not None:
			seller.rating = data.seller_rating
		return seller

	seller = Seller(
		platform=platform,
		name=data.seller_name.strip(),
		rating=data.seller_rating,
	)
	db.session.add(seller)
	return seller


def _find_or_create_listing(
	data: ScrapedProductData,
	product: Product,
	seller: Seller | None,
) -> Listing:
	platform = data.platform.strip()
	external_product_id = data.external_product_id.strip()
	product_url = data.product_url.strip()
	query = Listing.query.filter(
		Listing.platform == platform,
		Listing.external_product_id == external_product_id,
	)

	if seller is not None:
		listing = query.filter(Listing.seller_id == seller.id).order_by(Listing.id).first()
	else:
		listing = query.filter(
			Listing.seller_id.is_(None),
			Listing.url == product_url,
		).order_by(Listing.id).first()

	if listing is not None:
		return listing

	exact_url_matches = query.filter(Listing.url == product_url).order_by(Listing.id).all()
	if seller is None and len(exact_url_matches) == 1:
		return exact_url_matches[0]

	listing = Listing(
		product=product,
		seller=seller,
		platform=platform,
		external_product_id=external_product_id,
		url=product_url,
		current_price=data.current_price,
		old_price=data.old_price,
		discount_percentage=data.discount_percentage,
		currency=_clean_display_value(data.currency) or "TRY",
		availability=_clean_display_value(data.availability),
		visible_sales_count=data.visible_sales_count,
		last_scraped_at=data.scraped_at,
	)
	db.session.add(listing)
	return listing


def _update_listing(
	listing: Listing,
	data: ScrapedProductData,
	product: Product,
	seller: Seller | None,
) -> None:
	listing.product = product
	listing.seller = seller
	listing.platform = data.platform.strip()
	listing.external_product_id = data.external_product_id.strip()
	listing.url = data.product_url.strip()
	listing.current_price = data.current_price
	listing.old_price = data.old_price
	listing.discount_percentage = data.discount_percentage
	listing.currency = _clean_display_value(data.currency) or listing.currency or "TRY"
	listing.availability = _clean_display_value(data.availability)
	listing.visible_sales_count = data.visible_sales_count
	listing.last_scraped_at = data.scraped_at


def _get_missing_critical_fields(data: ScrapedProductData) -> list[str]:
	missing_fields: list[str] = []
	for field_name in (
		"platform",
		"product_name",
		"current_price",
		"external_product_id",
		"product_url",
		"scraped_at",
	):
		value = getattr(data, field_name, None)
		if not _has_value(value):
			missing_fields.append(field_name)

	return missing_fields


def _has_value(value: str | Decimal | object | None) -> bool:
	if value is None:
		return False
	if isinstance(value, str):
		return bool(value.strip())
	return True


def _normalize_lookup_value(value: str | None) -> str | None:
	if value is None:
		return None

	cleaned = value.strip().lower()
	if not cleaned:
		return None

	return cleaned


def _clean_display_value(value: str | None) -> str | None:
	if value is None:
		return None

	cleaned = value.strip()
	if not cleaned:
		return None

	return cleaned