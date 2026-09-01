"""Query helpers for product listing and product detail pages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, or_

from database.db import db
from database.models import Listing, PriceHistory, Product, Seller


ALLOWED_PRODUCT_SORTS = {
	"name",
	"price_asc",
	"price_desc",
	"updated_desc",
}


@dataclass(slots=True)
class ProductFilters:
	"""Validated filters for the products page."""

	q: str = ""
	brand: str = ""
	platform: str = ""
	sort: str = "updated_desc"
	page: int = 1
	page_size: int = 20

	@property
	def has_filters(self) -> bool:
		return bool(self.q or self.brand or self.platform or self.sort != "updated_desc")


@dataclass(slots=True)
class ProductListItem:
	"""Aggregated row for the products index table."""

	product_id: int
	brand: str | None
	name: str
	model: str | None
	platforms: tuple[str, ...]
	listing_count: int
	seller_count: int
	lowest_current_price: Decimal | None
	last_updated: datetime | None


@dataclass(slots=True)
class ProductListResult:
	"""Paginated products page payload."""

	items: list[ProductListItem]
	total_count: int
	page: int
	page_size: int
	total_pages: int
	filters: ProductFilters
	available_brands: list[str]
	available_platforms: list[str]
	sort_options: tuple[tuple[str, str], ...]


@dataclass(slots=True)
class ProductListingItem:
	"""Current marketplace listing displayed on the product detail page."""

	listing_id: int
	platform: str
	seller_name: str | None
	current_price: Decimal
	old_price: Decimal | None
	discount_percentage: Decimal | None
	availability: str | None
	currency: str
	last_updated: datetime | None
	is_lowest_price: bool


@dataclass(slots=True)
class ProductPriceHistoryItem:
	"""Historical price observation displayed on the product detail page."""

	history_id: int
	platform: str
	seller_name: str | None
	price: Decimal
	old_price: Decimal | None
	discount_percentage: Decimal | None
	recorded_at: datetime
	currency: str


@dataclass(slots=True)
class ProductDetailData:
	"""Full product detail page payload."""

	product: Product
	current_listings: list[ProductListingItem]
	price_history: list[ProductPriceHistoryItem]
	lowest_current_price: Decimal | None
	listing_count: int
	seller_count: int
	observation_count: int
	last_updated: datetime | None


def list_products(
	*,
	q: str | None = None,
	brand: str | None = None,
	platform: str | None = None,
	sort: str = "updated_desc",
	page: int = 1,
	page_size: int = 20,
) -> ProductListResult:
	"""Return paginated real product data with safe filters and sorting."""
	filters = ProductFilters(
		q=_clean_text(q) or "",
		brand=_clean_text(brand) or "",
		platform=_normalize_platform(platform) or "",
		sort=sort if sort in ALLOWED_PRODUCT_SORTS else "updated_desc",
		page=page if page > 0 else 1,
		page_size=page_size,
	)

	count_query = db.session.query(func.count(func.distinct(Product.id)))
	count_query = count_query.outerjoin(Listing, Listing.product_id == Product.id)
	count_query = _apply_product_filters(count_query, filters)
	total_count = count_query.scalar() or 0
	total_pages = max(1, (total_count + filters.page_size - 1) // filters.page_size)
	if filters.page > total_pages:
		filters.page = total_pages

	last_updated_expr = func.coalesce(Listing.last_scraped_at, Listing.updated_at, Listing.created_at)
	aggregation_query = (
		db.session.query(
			Product.id.label("product_id"),
			Product.brand.label("brand"),
			Product.name.label("name"),
			Product.model.label("model"),
			func.group_concat(func.distinct(Listing.platform)).label("platforms"),
			func.count(func.distinct(Listing.id)).label("listing_count"),
			func.count(func.distinct(Listing.seller_id)).label("seller_count"),
			func.min(Listing.current_price).label("lowest_current_price"),
			func.max(last_updated_expr).label("last_updated"),
		)
		.outerjoin(Listing, Listing.product_id == Product.id)
	)
	aggregation_query = _apply_product_filters(aggregation_query, filters)
	aggregation_query = aggregation_query.group_by(Product.id, Product.brand, Product.name, Product.model)
	aggregation_query = _apply_product_sort(aggregation_query, filters.sort)
	aggregation_query = aggregation_query.offset((filters.page - 1) * filters.page_size).limit(filters.page_size)

	items = [
		ProductListItem(
			product_id=row.product_id,
			brand=row.brand,
			name=row.name,
			model=row.model,
			platforms=_split_platforms(row.platforms),
			listing_count=row.listing_count,
			seller_count=row.seller_count,
			lowest_current_price=row.lowest_current_price,
			last_updated=row.last_updated,
		)
		for row in aggregation_query.all()
	]

	available_brands = [
		row[0]
		for row in (
			db.session.query(Product.brand)
			.filter(Product.brand.isnot(None), Product.brand != "")
			.distinct()
			.order_by(func.lower(Product.brand).asc())
			.all()
		)
	]
	available_platforms = [
		row[0]
		for row in (
			db.session.query(Listing.platform)
			.filter(Listing.platform.isnot(None), Listing.platform != "")
			.distinct()
			.order_by(Listing.platform.asc())
			.all()
		)
	]

	return ProductListResult(
		items=items,
		total_count=total_count,
		page=filters.page,
		page_size=filters.page_size,
		total_pages=total_pages,
		filters=filters,
		available_brands=available_brands,
		available_platforms=available_platforms,
		sort_options=(
			("updated_desc", "Recently updated"),
			("name", "Product name"),
			("price_asc", "Lowest price ascending"),
			("price_desc", "Lowest price descending"),
		),
	)


def get_product_detail(product_id: int) -> ProductDetailData | None:
	"""Return detailed product information for the product detail page."""
	product = Product.query.filter_by(id=product_id).first()
	if product is None:
		return None

	last_updated_expr = func.coalesce(Listing.last_scraped_at, Listing.updated_at, Listing.created_at)
	listing_rows = (
		db.session.query(
			Listing.id.label("listing_id"),
			Listing.platform.label("platform"),
			Seller.name.label("seller_name"),
			Listing.current_price.label("current_price"),
			Listing.old_price.label("old_price"),
			Listing.discount_percentage.label("discount_percentage"),
			Listing.availability.label("availability"),
			Listing.currency.label("currency"),
			last_updated_expr.label("last_updated"),
		)
		.outerjoin(Seller, Listing.seller_id == Seller.id)
		.filter(Listing.product_id == product_id)
		.order_by(Listing.current_price.asc(), last_updated_expr.desc(), Listing.id.asc())
		.all()
	)
	lowest_current_price = min((row.current_price for row in listing_rows), default=None)
	current_listings = [
		ProductListingItem(
			listing_id=row.listing_id,
			platform=row.platform,
			seller_name=row.seller_name,
			current_price=row.current_price,
			old_price=row.old_price,
			discount_percentage=row.discount_percentage,
			availability=row.availability,
			currency=row.currency,
			last_updated=row.last_updated,
			is_lowest_price=lowest_current_price is not None and row.current_price == lowest_current_price,
		)
		for row in listing_rows
	]

	history_rows = (
		db.session.query(
			PriceHistory.id.label("history_id"),
			Listing.platform.label("platform"),
			Seller.name.label("seller_name"),
			PriceHistory.price.label("price"),
			PriceHistory.old_price.label("old_price"),
			PriceHistory.discount_percentage.label("discount_percentage"),
			PriceHistory.recorded_at.label("recorded_at"),
			Listing.currency.label("currency"),
		)
		.join(Listing, PriceHistory.listing_id == Listing.id)
		.outerjoin(Seller, Listing.seller_id == Seller.id)
		.filter(Listing.product_id == product_id)
		.order_by(PriceHistory.recorded_at.desc(), PriceHistory.id.desc())
		.limit(50)
		.all()
	)
	price_history = [
		ProductPriceHistoryItem(
			history_id=row.history_id,
			platform=row.platform,
			seller_name=row.seller_name,
			price=row.price,
			old_price=row.old_price,
			discount_percentage=row.discount_percentage,
			recorded_at=row.recorded_at,
			currency=row.currency,
		)
		for row in history_rows
	]

	last_updated = max((row.last_updated for row in listing_rows if row.last_updated is not None), default=None)
	seller_names = {row.seller_name for row in listing_rows if row.seller_name is not None}

	return ProductDetailData(
		product=product,
		current_listings=current_listings,
		price_history=price_history,
		lowest_current_price=lowest_current_price,
		listing_count=len(current_listings),
		seller_count=len(seller_names),
		observation_count=len(price_history),
		last_updated=last_updated,
	)


def _apply_product_filters(query, filters: ProductFilters):
	if filters.q:
		pattern = f"%{filters.q}%"
		query = query.filter(
			or_(
				Product.name.ilike(pattern),
				Product.brand.ilike(pattern),
				Product.model.ilike(pattern),
			)
		)
	if filters.brand:
		query = query.filter(Product.brand == filters.brand)
	if filters.platform:
		query = query.filter(Listing.platform == filters.platform)
	return query


def _apply_product_sort(query, sort: str):
	if sort == "name":
		return query.order_by(func.lower(Product.name).asc(), Product.id.asc())
	if sort == "price_asc":
		return query.order_by(func.coalesce(func.min(Listing.current_price), 999999999).asc(), func.lower(Product.name).asc())
	if sort == "price_desc":
		return query.order_by(func.coalesce(func.min(Listing.current_price), -1).desc(), func.lower(Product.name).asc())
	last_updated_expr = func.coalesce(Listing.last_scraped_at, Listing.updated_at, Listing.created_at)
	return query.order_by(func.max(last_updated_expr).desc(), func.lower(Product.name).asc())


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


def _split_platforms(value: str | None) -> tuple[str, ...]:
	if value is None:
		return tuple()
	platforms = [item.strip() for item in value.split(",") if item and item.strip()]
	return tuple(platforms)
