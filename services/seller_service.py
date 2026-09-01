"""Query helpers for seller listing and seller detail pages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func

from database.db import db
from database.models import Listing, Product, Seller


@dataclass(slots=True)
class SellerFilters:
	"""Validated filters for the sellers page."""

	q: str = ""
	platform: str = ""
	page: int = 1
	page_size: int = 20

	@property
	def has_filters(self) -> bool:
		return bool(self.q or self.platform)


@dataclass(slots=True)
class SellerListItem:
	"""Aggregated row for the sellers index table."""

	seller_id: int
	name: str
	platform: str
	rating: Decimal | None
	listing_count: int
	lowest_current_price: Decimal | None
	average_current_price: Decimal | None
	last_activity: datetime | None


@dataclass(slots=True)
class SellerListResult:
	"""Paginated sellers page payload."""

	items: list[SellerListItem]
	total_count: int
	page: int
	page_size: int
	total_pages: int
	filters: SellerFilters
	available_platforms: list[str]


@dataclass(slots=True)
class SellerProductItem:
	"""Product row displayed on the seller detail page."""

	product_id: int
	brand: str | None
	product_name: str
	model: str | None
	current_price: Decimal
	currency: str
	last_updated: datetime | None


@dataclass(slots=True)
class SellerDetailData:
	"""Full seller detail payload."""

	seller: Seller
	listing_count: int
	lowest_current_price: Decimal | None
	average_current_price: Decimal | None
	last_activity: datetime | None
	products: list[SellerProductItem]


def list_sellers(
	*,
	q: str | None = None,
	platform: str | None = None,
	page: int = 1,
	page_size: int = 20,
) -> SellerListResult:
	"""Return paginated real seller data with safe filters."""
	filters = SellerFilters(
		q=_clean_text(q) or "",
		platform=_normalize_platform(platform) or "",
		page=page if page > 0 else 1,
		page_size=page_size,
	)

	count_query = db.session.query(func.count(func.distinct(Seller.id))).outerjoin(Listing, Listing.seller_id == Seller.id)
	count_query = _apply_seller_filters(count_query, filters)
	total_count = count_query.scalar() or 0
	total_pages = max(1, (total_count + filters.page_size - 1) // filters.page_size)
	if filters.page > total_pages:
		filters.page = total_pages

	last_activity_expr = func.coalesce(Listing.last_scraped_at, Listing.updated_at, Listing.created_at, Seller.updated_at)
	rows = (
		db.session.query(
			Seller.id.label("seller_id"),
			Seller.name.label("name"),
			Seller.platform.label("platform"),
			Seller.rating.label("rating"),
			func.count(func.distinct(Listing.id)).label("listing_count"),
			func.min(Listing.current_price).label("lowest_current_price"),
			func.avg(Listing.current_price).label("average_current_price"),
			func.max(last_activity_expr).label("last_activity"),
		)
		.outerjoin(Listing, Listing.seller_id == Seller.id)
	)
	rows = _apply_seller_filters(rows, filters)
	rows = rows.group_by(Seller.id, Seller.name, Seller.platform, Seller.rating)
	rows = rows.order_by(func.max(last_activity_expr).desc(), func.lower(Seller.name).asc())
	rows = rows.offset((filters.page - 1) * filters.page_size).limit(filters.page_size)

	items = [
		SellerListItem(
			seller_id=row.seller_id,
			name=row.name,
			platform=row.platform,
			rating=row.rating,
			listing_count=row.listing_count,
			lowest_current_price=row.lowest_current_price,
			average_current_price=_normalize_decimal(row.average_current_price),
			last_activity=row.last_activity,
		)
		for row in rows.all()
	]

	available_platforms = [
		row[0]
		for row in (
			db.session.query(Seller.platform)
			.filter(Seller.platform.isnot(None), Seller.platform != "")
			.distinct()
			.order_by(Seller.platform.asc())
			.all()
		)
	]

	return SellerListResult(
		items=items,
		total_count=total_count,
		page=filters.page,
		page_size=filters.page_size,
		total_pages=total_pages,
		filters=filters,
		available_platforms=available_platforms,
	)


def get_seller_detail(seller_id: int) -> SellerDetailData | None:
	"""Return detailed seller information and linked products."""
	seller = Seller.query.filter_by(id=seller_id).first()
	if seller is None:
		return None

	last_updated_expr = func.coalesce(Listing.last_scraped_at, Listing.updated_at, Listing.created_at)
	rows = (
		db.session.query(
			Product.id.label("product_id"),
			Product.brand.label("brand"),
			Product.name.label("product_name"),
			Product.model.label("model"),
			Listing.current_price.label("current_price"),
			Listing.currency.label("currency"),
			last_updated_expr.label("last_updated"),
		)
		.join(Product, Listing.product_id == Product.id)
		.filter(Listing.seller_id == seller_id)
		.order_by(last_updated_expr.desc(), func.lower(Product.name).asc())
		.all()
	)
	products = [
		SellerProductItem(
			product_id=row.product_id,
			brand=row.brand,
			product_name=row.product_name,
			model=row.model,
			current_price=row.current_price,
			currency=row.currency,
			last_updated=row.last_updated,
		)
		for row in rows
	]
	prices = [row.current_price for row in rows]
	last_activity = max((row.last_updated for row in rows if row.last_updated is not None), default=seller.updated_at)
	average_current_price = (sum(prices) / len(prices)) if prices else None

	return SellerDetailData(
		seller=seller,
		listing_count=len(rows),
		lowest_current_price=min(prices) if prices else None,
		average_current_price=average_current_price,
		last_activity=last_activity,
		products=products,
	)


def _apply_seller_filters(query, filters: SellerFilters):
	if filters.q:
		query = query.filter(Seller.name.ilike(f"%{filters.q}%"))
	if filters.platform:
		query = query.filter(Seller.platform == filters.platform)
	return query


def _clean_text(value: str | None) -> str | None:
	if value is None:
		return None
	cleaned = value.strip()
	if not cleaned:
		return None
	return cleaned


def _normalize_platform(value: str | None) -> str | None:
	cleaned = _clean_text(value)
	if cleaned is None:
		return None
	return cleaned.lower()


def _normalize_decimal(value: Decimal | float | int | None) -> Decimal | None:
	if value is None:
		return None
	if isinstance(value, Decimal):
		return value.quantize(Decimal("0.01"))
	return Decimal(str(value)).quantize(Decimal("0.01"))
