"""Price history analysis helpers for listing and dashboard intelligence."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

from sqlalchemy import func

from database.db import db
from database.models import Listing, PriceHistory, Product, Seller


MONEY_QUANTIZE = Decimal("0.01")
PERCENT_QUANTIZE = Decimal("0.01")
ALLOWED_HISTORY_RANGES: dict[str, timedelta | None] = {
	"7d": timedelta(days=7),
	"30d": timedelta(days=30),
	"90d": timedelta(days=90),
	"all": None,
}
DEFAULT_HISTORY_RANGE = "30d"
DASHBOARD_CHANGE_WINDOW_DAYS = 7
DASHBOARD_BIGGEST_DROPS_LIMIT = 5
DASHBOARD_BIGGEST_INCREASES_LIMIT = 5
DASHBOARD_RECENT_CHANGES_LIMIT = 10


@dataclass(slots=True)
class PriceObservation:
	"""Normalized price observation used by analytics helpers."""

	history_id: int
	listing_id: int
	price: Decimal | None
	recorded_at: datetime
	currency: str = "TRY"
	product_id: int | None = None
	product_name: str | None = None
	platform: str | None = None
	seller_name: str | None = None


@dataclass(slots=True)
class PriceChangeEvent:
	"""A real price change derived from distinct consecutive observations."""

	history_id: int
	listing_id: int
	product_id: int | None
	product_name: str | None
	platform: str | None
	seller_name: str | None
	previous_price: Decimal
	current_price: Decimal
	absolute_change: Decimal
	percentage_change: Decimal | None
	recorded_at: datetime
	currency: str = "TRY"


@dataclass(slots=True)
class ListingPriceMetrics:
	"""Computed metrics for a listing's observation history."""

	current_price: Decimal | None
	previous_distinct_price: Decimal | None
	absolute_change: Decimal | None
	percentage_change: Decimal | None
	observed_min_price: Decimal | None
	observed_max_price: Decimal | None
	observed_average_price: Decimal | None
	first_observed_price: Decimal | None
	change_since_first: Decimal | None
	change_since_first_percentage: Decimal | None
	observation_count: int
	actual_price_change_count: int
	first_observed_at: datetime | None
	last_observed_at: datetime | None


@dataclass(slots=True)
class ListingAnalysisItem:
	"""Product-detail analytics for one current listing."""

	listing_id: int
	product_id: int
	product_name: str
	platform: str
	seller_name: str | None
	currency: str
	availability: str | None
	last_updated: datetime | None
	current_price: Decimal | None
	previous_distinct_price: Decimal | None
	absolute_change: Decimal | None
	percentage_change: Decimal | None
	observed_min_price: Decimal | None
	observed_max_price: Decimal | None
	observed_average_price: Decimal | None
	first_observed_price: Decimal | None
	change_since_first: Decimal | None
	change_since_first_percentage: Decimal | None
	observation_count: int
	actual_price_change_count: int
	first_observed_at: datetime | None
	last_observed_at: datetime | None
	is_current_cheapest: bool


@dataclass(slots=True)
class ProductCurrentMetrics:
	"""Current and observed analytics for a product across all listings."""

	cheapest_current_listing_id: int | None
	highest_current_listing_id: int | None
	current_min_price: Decimal | None
	current_max_price: Decimal | None
	current_average_price: Decimal | None
	seller_count: int
	active_listing_count: int
	price_spread_amount: Decimal | None
	price_spread_percentage: Decimal | None
	observed_min_price: Decimal | None
	observed_max_price: Decimal | None
	observation_count: int
	actual_price_change_count: int
	last_updated: datetime | None


@dataclass(slots=True)
class SellerComparisonItem:
	"""Current seller comparison row for the product detail page."""

	listing_id: int
	seller_name: str | None
	platform: str
	current_price: Decimal | None
	difference_from_cheapest: Decimal | None
	difference_percentage: Decimal | None
	last_updated: datetime | None
	currency: str
	is_cheapest: bool
	observation_count: int
	actual_price_change_count: int


@dataclass(slots=True)
class ObservationHistoryItem:
	"""Rendered history row for a selected listing."""

	history_id: int
	listing_id: int
	seller_name: str | None
	platform: str
	price: Decimal
	previous_distinct_price: Decimal | None
	absolute_change: Decimal | None
	percentage_change: Decimal | None
	recorded_at: datetime
	currency: str
	is_actual_change: bool


@dataclass(slots=True)
class RangeOption:
	"""Selectable history range for the product detail page."""

	key: str
	label: str
	is_active: bool


@dataclass(slots=True)
class ChartPoint:
	"""Plotted point for the native SVG price chart."""

	x: int
	y: int
	price: Decimal
	recorded_at_label: str
	is_actual_change: bool


@dataclass(slots=True)
class ChartAxisLabel:
	"""Axis label for the native SVG chart."""

	x: int
	label: str


@dataclass(slots=True)
class ChartAxisTick:
	"""Horizontal grid tick for the native SVG chart."""

	y: int
	label: str


@dataclass(slots=True)
class PriceHistoryChart:
	"""Precomputed native SVG chart payload for a selected listing."""

	series_label: str
	polyline_points: str
	points: list[ChartPoint]
	x_axis_labels: list[ChartAxisLabel]
	y_axis_ticks: list[ChartAxisTick]
	min_price: Decimal | None
	max_price: Decimal | None
	point_count: int
	empty_message: str | None


@dataclass(slots=True)
class ProductPriceIntelligencePage:
	"""Complete product detail analytics payload."""

	product: Product
	metrics: ProductCurrentMetrics
	selected_range: str
	range_options: list[RangeOption]
	selected_listing_id: int | None
	selected_listing: ListingAnalysisItem | None
	seller_comparison: list[SellerComparisonItem]
	history_items: list[ObservationHistoryItem]
	history_page: int
	history_page_size: int
	history_total_count: int
	history_total_pages: int
	chart: PriceHistoryChart


@dataclass(slots=True)
class DashboardPriceChangeItem:
	"""Dashboard row representing one real price move."""

	product_id: int
	listing_id: int
	product_name: str
	seller_name: str | None
	platform: str
	old_price: Decimal
	new_price: Decimal
	absolute_change: Decimal
	percentage_change: Decimal | None
	recorded_at: datetime
	currency: str


@dataclass(slots=True)
class DashboardPriceIntelligence:
	"""Dashboard price mover groups for a recent time window."""

	window_label: str
	biggest_drops: list[DashboardPriceChangeItem]
	biggest_increases: list[DashboardPriceChangeItem]
	recent_changes: list[DashboardPriceChangeItem]


def analyze_listing_observations(
	observations: Sequence[PriceObservation],
	*,
	current_price: Decimal | None = None,
) -> ListingPriceMetrics:
	"""Compute listing-level metrics while ignoring repeated identical prices for change detection."""
	ordered_observations = sorted(observations, key=lambda item: (item.recorded_at, item.history_id))
	valid_observations = [item for item in ordered_observations if _is_valid_price(item.price)]

	if not valid_observations:
		valid_current_price = _normalize_money(current_price)
		return ListingPriceMetrics(
			current_price=valid_current_price,
			previous_distinct_price=None,
			absolute_change=None,
			percentage_change=None,
			observed_min_price=valid_current_price,
			observed_max_price=valid_current_price,
			observed_average_price=valid_current_price,
			first_observed_price=valid_current_price,
			change_since_first=None,
			change_since_first_percentage=None,
			observation_count=0,
			actual_price_change_count=0,
			first_observed_at=None,
			last_observed_at=None,
		)

	prices = [_normalize_money(item.price) for item in valid_observations]
	first_price = prices[0]
	last_price = prices[-1]
	active_current_price = _normalize_money(current_price) or last_price
	distinct_prices: list[Decimal] = []
	last_distinct_price: Decimal | None = None

	for price in prices:
		if last_distinct_price is None or price != last_distinct_price:
			distinct_prices.append(price)
			last_distinct_price = price

	previous_distinct_price = distinct_prices[-2] if len(distinct_prices) >= 2 else None
	absolute_change = None
	percentage_change = None
	if previous_distinct_price is not None:
		absolute_change = _normalize_money(active_current_price - previous_distinct_price)
		percentage_change = _calculate_percentage_change(active_current_price, previous_distinct_price)

	change_since_first = None
	change_since_first_percentage = None
	if first_price is not None and active_current_price is not None and active_current_price != first_price:
		change_since_first = _normalize_money(active_current_price - first_price)
		change_since_first_percentage = _calculate_percentage_change(active_current_price, first_price)

	observed_average_price = _normalize_money(sum(prices, Decimal("0.00")) / Decimal(len(prices)))

	return ListingPriceMetrics(
		current_price=active_current_price,
		previous_distinct_price=previous_distinct_price,
		absolute_change=absolute_change,
		percentage_change=percentage_change,
		observed_min_price=min(prices),
		observed_max_price=max(prices),
		observed_average_price=observed_average_price,
		first_observed_price=first_price,
		change_since_first=change_since_first,
		change_since_first_percentage=change_since_first_percentage,
		observation_count=len(valid_observations),
		actual_price_change_count=max(0, len(distinct_prices) - 1),
		first_observed_at=valid_observations[0].recorded_at,
		last_observed_at=valid_observations[-1].recorded_at,
	)


def get_product_price_intelligence_page(
	product_id: int,
	*,
	range_key: str | None = None,
	selected_listing_id: int | None = None,
	history_page: int = 1,
	history_page_size: int = 20,
) -> ProductPriceIntelligencePage | None:
	"""Return full product-detail analytics, including chart and seller comparison."""
	product = Product.query.filter_by(id=product_id).first()
	if product is None:
		return None

	last_updated_expr = func.coalesce(Listing.last_scraped_at, Listing.updated_at, Listing.created_at)
	listing_rows = (
		db.session.query(
			Listing.id.label("listing_id"),
			Listing.product_id.label("product_id"),
			Product.name.label("product_name"),
			Listing.platform.label("platform"),
			Seller.name.label("seller_name"),
			Listing.current_price.label("current_price"),
			Listing.currency.label("currency"),
			Listing.availability.label("availability"),
			last_updated_expr.label("last_updated"),
		)
		.join(Product, Listing.product_id == Product.id)
		.outerjoin(Seller, Listing.seller_id == Seller.id)
		.filter(Listing.product_id == product_id)
		.order_by(Listing.current_price.asc(), last_updated_expr.desc(), Listing.id.asc())
		.all()
	)

	history_rows = (
		db.session.query(
			PriceHistory.id.label("history_id"),
			PriceHistory.listing_id.label("listing_id"),
			PriceHistory.price.label("price"),
			PriceHistory.recorded_at.label("recorded_at"),
			Listing.currency.label("currency"),
			Listing.platform.label("platform"),
			Seller.name.label("seller_name"),
			Product.id.label("product_id"),
			Product.name.label("product_name"),
		)
		.join(Listing, PriceHistory.listing_id == Listing.id)
		.join(Product, Listing.product_id == Product.id)
		.outerjoin(Seller, Listing.seller_id == Seller.id)
		.filter(Listing.product_id == product_id)
		.order_by(Listing.id.asc(), PriceHistory.recorded_at.asc(), PriceHistory.id.asc())
		.all()
	)

	observations_by_listing: dict[int, list[PriceObservation]] = defaultdict(list)
	for row in history_rows:
		observations_by_listing[row.listing_id].append(
			PriceObservation(
				history_id=row.history_id,
				listing_id=row.listing_id,
				price=row.price,
				recorded_at=_ensure_utc(row.recorded_at),
				currency=row.currency,
				product_id=row.product_id,
				product_name=row.product_name,
				platform=row.platform,
				seller_name=row.seller_name,
			)
		)

	listing_analyses = [
		_build_listing_analysis_item(row, observations_by_listing.get(row.listing_id, []))
		for row in listing_rows
	]
	listing_analyses.sort(
		key=lambda item: (
			item.current_price is None,
			item.current_price if item.current_price is not None else Decimal("999999999.99"),
			item.last_updated is None,
			item.last_updated or datetime.min.replace(tzinfo=timezone.utc),
			item.listing_id,
		)
	)

	metrics = _build_product_current_metrics(listing_analyses, history_rows)
	selected_range = normalize_history_range(range_key)
	selected_listing = _select_listing(listing_analyses, selected_listing_id, metrics.cheapest_current_listing_id)
	selected_listing_id = selected_listing.listing_id if selected_listing is not None else None

	selected_observations = observations_by_listing.get(selected_listing_id, []) if selected_listing_id is not None else []
	filtered_observations = _filter_observations_by_range(selected_observations, selected_range)
	history_items = _build_history_items(filtered_observations)
	history_total_count = len(history_items)
	history_total_pages = max(1, (history_total_count + history_page_size - 1) // history_page_size)
	history_page = min(max(history_page, 1), history_total_pages)
	reversed_history_items = list(reversed(history_items))
	start_index = (history_page - 1) * history_page_size
	end_index = start_index + history_page_size
	paged_history_items = reversed_history_items[start_index:end_index]

	chart = _build_price_history_chart(filtered_observations, selected_listing)
	return ProductPriceIntelligencePage(
		product=product,
		metrics=metrics,
		selected_range=selected_range,
		range_options=[
			RangeOption(key="7d", label="7D", is_active=selected_range == "7d"),
			RangeOption(key="30d", label="30D", is_active=selected_range == "30d"),
			RangeOption(key="90d", label="90D", is_active=selected_range == "90d"),
			RangeOption(key="all", label="All", is_active=selected_range == "all"),
		],
		selected_listing_id=selected_listing_id,
		selected_listing=selected_listing,
		seller_comparison=_build_seller_comparison_items(listing_analyses, metrics.current_min_price),
		history_items=paged_history_items,
		history_page=history_page,
		history_page_size=history_page_size,
		history_total_count=history_total_count,
		history_total_pages=history_total_pages,
		chart=chart,
	)


def get_dashboard_price_intelligence(
	*,
	window_days: int = DASHBOARD_CHANGE_WINDOW_DAYS,
	drop_limit: int = DASHBOARD_BIGGEST_DROPS_LIMIT,
	increase_limit: int = DASHBOARD_BIGGEST_INCREASES_LIMIT,
	recent_limit: int = DASHBOARD_RECENT_CHANGES_LIMIT,
) -> DashboardPriceIntelligence:
	"""Return recent real price movers from the database without counting duplicate observations."""
	rows = (
		db.session.query(
			PriceHistory.id.label("history_id"),
			PriceHistory.listing_id.label("listing_id"),
			PriceHistory.price.label("price"),
			PriceHistory.recorded_at.label("recorded_at"),
			Listing.currency.label("currency"),
			Listing.platform.label("platform"),
			Product.id.label("product_id"),
			Product.name.label("product_name"),
			Seller.name.label("seller_name"),
		)
		.join(Listing, PriceHistory.listing_id == Listing.id)
		.join(Product, Listing.product_id == Product.id)
		.outerjoin(Seller, Listing.seller_id == Seller.id)
		.order_by(Listing.id.asc(), PriceHistory.recorded_at.asc(), PriceHistory.id.asc())
		.all()
	)

	observations_by_listing: dict[int, list[PriceObservation]] = defaultdict(list)
	for row in rows:
		observations_by_listing[row.listing_id].append(
			PriceObservation(
				history_id=row.history_id,
				listing_id=row.listing_id,
				price=row.price,
				recorded_at=_ensure_utc(row.recorded_at),
				currency=row.currency,
				product_id=row.product_id,
				product_name=row.product_name,
				platform=row.platform,
				seller_name=row.seller_name,
			)
		)

	cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
	all_change_items: list[DashboardPriceChangeItem] = []
	for observations in observations_by_listing.values():
		for event in extract_price_change_events(observations):
			if _ensure_utc(event.recorded_at) < cutoff:
				continue
			if event.product_id is None or event.product_name is None or event.platform is None:
				continue
			all_change_items.append(
				DashboardPriceChangeItem(
					product_id=event.product_id,
					listing_id=event.listing_id,
					product_name=event.product_name,
					seller_name=event.seller_name,
					platform=event.platform,
					old_price=event.previous_price,
					new_price=event.current_price,
					absolute_change=event.absolute_change,
					percentage_change=event.percentage_change,
					recorded_at=event.recorded_at,
					currency=event.currency,
				)
			)

	biggest_drops = sorted(
		[item for item in all_change_items if item.absolute_change < 0],
		key=lambda item: (
			item.percentage_change if item.percentage_change is not None else Decimal("0.00"),
			item.absolute_change,
			item.recorded_at,
		),
	)[:drop_limit]
	biggest_increases = sorted(
		[item for item in all_change_items if item.absolute_change > 0],
		key=lambda item: (
			-(item.percentage_change if item.percentage_change is not None else Decimal("0.00")),
			-item.absolute_change,
			item.recorded_at,
		),
	)[:increase_limit]
	recent_changes = sorted(all_change_items, key=lambda item: (item.recorded_at, item.listing_id), reverse=True)[:recent_limit]

	return DashboardPriceIntelligence(
		window_label=f"Last {window_days} days",
		biggest_drops=biggest_drops,
		biggest_increases=biggest_increases,
		recent_changes=recent_changes,
	)


def extract_price_change_events(observations: Sequence[PriceObservation]) -> list[PriceChangeEvent]:
	"""Return only real price-change events for a listing, skipping repeated identical observations."""
	ordered_observations = sorted(observations, key=lambda item: (item.recorded_at, item.history_id))
	valid_observations = [item for item in ordered_observations if _is_valid_price(item.price)]

	change_events: list[PriceChangeEvent] = []
	previous_distinct_price: Decimal | None = None

	for observation in valid_observations:
		current_price = _normalize_money(observation.price)
		if previous_distinct_price is None:
			previous_distinct_price = current_price
			continue
		if current_price == previous_distinct_price:
			continue

		change_events.append(
			PriceChangeEvent(
				history_id=observation.history_id,
				listing_id=observation.listing_id,
				product_id=observation.product_id,
				product_name=observation.product_name,
				platform=observation.platform,
				seller_name=observation.seller_name,
				previous_price=previous_distinct_price,
				current_price=current_price,
				absolute_change=_normalize_money(current_price - previous_distinct_price),
				percentage_change=_calculate_percentage_change(current_price, previous_distinct_price),
				recorded_at=observation.recorded_at,
				currency=observation.currency,
			)
		)
		previous_distinct_price = current_price

	return change_events


def normalize_history_range(value: str | None) -> str:
	"""Return a safe history range key for query parameters."""
	cleaned = (value or "").strip().lower()
	if cleaned in ALLOWED_HISTORY_RANGES:
		return cleaned
	return DEFAULT_HISTORY_RANGE


def _build_listing_analysis_item(row, observations: Sequence[PriceObservation]) -> ListingAnalysisItem:
	metrics = analyze_listing_observations(observations, current_price=row.current_price)
	return ListingAnalysisItem(
		listing_id=row.listing_id,
		product_id=row.product_id,
		product_name=row.product_name,
		platform=row.platform,
		seller_name=row.seller_name,
		currency=row.currency,
		availability=row.availability,
		last_updated=_ensure_optional_utc(row.last_updated),
		current_price=metrics.current_price,
		previous_distinct_price=metrics.previous_distinct_price,
		absolute_change=metrics.absolute_change,
		percentage_change=metrics.percentage_change,
		observed_min_price=metrics.observed_min_price,
		observed_max_price=metrics.observed_max_price,
		observed_average_price=metrics.observed_average_price,
		first_observed_price=metrics.first_observed_price,
		change_since_first=metrics.change_since_first,
		change_since_first_percentage=metrics.change_since_first_percentage,
		observation_count=metrics.observation_count,
		actual_price_change_count=metrics.actual_price_change_count,
		first_observed_at=metrics.first_observed_at,
		last_observed_at=metrics.last_observed_at,
		is_current_cheapest=False,
	)


def _build_product_current_metrics(listings: Sequence[ListingAnalysisItem], history_rows: Sequence[object]) -> ProductCurrentMetrics:
	current_prices = [item.current_price for item in listings if _is_valid_price(item.current_price)]
	current_min_price = min(current_prices) if current_prices else None
	current_max_price = max(current_prices) if current_prices else None
	current_average_price = None
	if current_prices:
		current_average_price = _normalize_money(sum(current_prices, Decimal("0.00")) / Decimal(len(current_prices)))

	cheapest_current_listing_id = next((item.listing_id for item in listings if item.current_price == current_min_price), None)
	highest_current_listing_id = next((item.listing_id for item in listings if item.current_price == current_max_price), None)
	for item in listings:
		item.is_current_cheapest = current_min_price is not None and item.current_price == current_min_price

	observed_prices = [
		_normalize_money(row.price)
		for row in history_rows
		if _is_valid_price(getattr(row, "price", None))
	]
	observed_prices = [item for item in observed_prices if item is not None]
	if not observed_prices:
		observed_prices = [item for item in current_prices if item is not None]

	observation_count = sum(item.observation_count for item in listings)
	actual_price_change_count = sum(item.actual_price_change_count for item in listings)
	seller_count = len({item.seller_name for item in listings if item.seller_name})
	price_spread_amount = None
	price_spread_percentage = None
	if current_min_price is not None and current_max_price is not None:
		price_spread_amount = _normalize_money(current_max_price - current_min_price)
		price_spread_percentage = _calculate_percentage_change(current_max_price, current_min_price)
	last_updated = max((item.last_updated for item in listings if item.last_updated is not None), default=None)

	return ProductCurrentMetrics(
		cheapest_current_listing_id=cheapest_current_listing_id,
		highest_current_listing_id=highest_current_listing_id,
		current_min_price=current_min_price,
		current_max_price=current_max_price,
		current_average_price=current_average_price,
		seller_count=seller_count,
		active_listing_count=len(listings),
		price_spread_amount=price_spread_amount,
		price_spread_percentage=price_spread_percentage,
		observed_min_price=min(observed_prices) if observed_prices else None,
		observed_max_price=max(observed_prices) if observed_prices else None,
		observation_count=observation_count,
		actual_price_change_count=actual_price_change_count,
		last_updated=last_updated,
	)


def _select_listing(
	listings: Sequence[ListingAnalysisItem],
	requested_listing_id: int | None,
	default_listing_id: int | None,
) -> ListingAnalysisItem | None:
	if not listings:
		return None
	for listing in listings:
		if requested_listing_id is not None and listing.listing_id == requested_listing_id:
			return listing
	for listing in listings:
		if default_listing_id is not None and listing.listing_id == default_listing_id:
			return listing
	return listings[0]


def _filter_observations_by_range(observations: Sequence[PriceObservation], range_key: str) -> list[PriceObservation]:
	ordered = sorted(observations, key=lambda item: (item.recorded_at, item.history_id))
	window = ALLOWED_HISTORY_RANGES.get(range_key)
	if window is None:
		return ordered
	cutoff = datetime.now(timezone.utc) - window
	return [item for item in ordered if _ensure_utc(item.recorded_at) >= cutoff]


def _build_seller_comparison_items(
	listings: Sequence[ListingAnalysisItem],
	cheapest_current_price: Decimal | None,
) -> list[SellerComparisonItem]:
	items = [
		SellerComparisonItem(
			listing_id=item.listing_id,
			seller_name=item.seller_name,
			platform=item.platform,
			current_price=item.current_price,
			difference_from_cheapest=None if item.current_price is None or cheapest_current_price is None else _normalize_money(item.current_price - cheapest_current_price),
			difference_percentage=None if item.current_price is None or cheapest_current_price is None else _calculate_percentage_change(item.current_price, cheapest_current_price),
			last_updated=item.last_updated,
			currency=item.currency,
			is_cheapest=item.is_current_cheapest,
			observation_count=item.observation_count,
			actual_price_change_count=item.actual_price_change_count,
		)
		for item in listings
	]
	items.sort(
		key=lambda item: (
			item.current_price is None,
			item.current_price if item.current_price is not None else Decimal("999999999.99"),
			item.last_updated is None,
			item.last_updated or datetime.min.replace(tzinfo=timezone.utc),
			item.listing_id,
		)
	)
	return items


def _build_history_items(observations: Sequence[PriceObservation]) -> list[ObservationHistoryItem]:
	ordered = [item for item in sorted(observations, key=lambda obs: (obs.recorded_at, obs.history_id)) if _is_valid_price(item.price)]
	results: list[ObservationHistoryItem] = []
	last_distinct_price: Decimal | None = None

	for observation in ordered:
		price = _normalize_money(observation.price)
		if price is None:
			continue
		previous_distinct_price = None
		absolute_change = None
		percentage_change = None
		is_actual_change = False
		if last_distinct_price is None:
			last_distinct_price = price
		elif price != last_distinct_price:
			previous_distinct_price = last_distinct_price
			absolute_change = _normalize_money(price - last_distinct_price)
			percentage_change = _calculate_percentage_change(price, last_distinct_price)
			last_distinct_price = price
			is_actual_change = True

		results.append(
			ObservationHistoryItem(
				history_id=observation.history_id,
				listing_id=observation.listing_id,
				seller_name=observation.seller_name,
				platform=observation.platform or "other",
				price=price,
				previous_distinct_price=previous_distinct_price,
				absolute_change=absolute_change,
				percentage_change=percentage_change,
				recorded_at=observation.recorded_at,
				currency=observation.currency,
				is_actual_change=is_actual_change,
			)
		)

	return results


def _build_price_history_chart(
	observations: Sequence[PriceObservation],
	selected_listing: ListingAnalysisItem | None,
) -> PriceHistoryChart:
	valid_observations = [item for item in observations if _is_valid_price(item.price)]
	if selected_listing is None:
		return PriceHistoryChart(
			series_label="No listing selected",
			polyline_points="",
			points=[],
			x_axis_labels=[],
			y_axis_ticks=[],
			min_price=None,
			max_price=None,
			point_count=0,
			empty_message="No listing is available for this product yet.",
		)
	if not valid_observations:
		return PriceHistoryChart(
			series_label=_build_series_label(selected_listing),
			polyline_points="",
			points=[],
			x_axis_labels=[],
			y_axis_ticks=[],
			min_price=None,
			max_price=None,
			point_count=0,
			empty_message="No price observations exist for the selected range.",
		)

	history_items = _build_history_items(valid_observations)
	prices = [item.price for item in history_items]
	min_price = min(prices)
	max_price = max(prices)
	chart_width = 880
	chart_height = 300
	left_padding = 56
	right_padding = 20
	top_padding = 18
	bottom_padding = 34
	usable_width = chart_width - left_padding - right_padding
	usable_height = chart_height - top_padding - bottom_padding
	price_range = max_price - min_price
	if price_range == Decimal("0.00"):
		price_range = max(min_price * Decimal("0.02"), Decimal("1.00"))
		min_price = _normalize_money(min_price - (price_range / Decimal("2"))) or min_price
		max_price = _normalize_money(max_price + (price_range / Decimal("2"))) or max_price
		price_range = max_price - min_price

	points: list[ChartPoint] = []
	polyline_parts: list[str] = []
	for index, item in enumerate(history_items):
		x = left_padding if len(history_items) == 1 else left_padding + round((usable_width * index) / (len(history_items) - 1))
		relative = (item.price - min_price) / price_range if price_range > 0 else Decimal("0")
		y = top_padding + round((Decimal(1) - relative) * usable_height)
		point = ChartPoint(
			x=x,
			y=y,
			price=item.price,
			recorded_at_label=_format_chart_timestamp(item.recorded_at),
			is_actual_change=item.is_actual_change,
		)
		points.append(point)
		polyline_parts.append(f"{x},{y}")

	y_axis_ticks = _build_y_axis_ticks(min_price, max_price, top_padding, usable_height)
	x_axis_labels = _build_x_axis_labels(points)
	return PriceHistoryChart(
		series_label=_build_series_label(selected_listing),
		polyline_points=" ".join(polyline_parts),
		points=points,
		x_axis_labels=x_axis_labels,
		y_axis_ticks=y_axis_ticks,
		min_price=min(prices),
		max_price=max(prices),
		point_count=len(points),
		empty_message=None,
	)


def _build_series_label(selected_listing: ListingAnalysisItem) -> str:
	seller_label = selected_listing.seller_name or "Unknown seller"
	return f"{seller_label} / {selected_listing.platform}"


def _build_y_axis_ticks(
	min_price: Decimal,
	max_price: Decimal,
	top_padding: int,
	usable_height: int,
) -> list[ChartAxisTick]:
	ticks: list[ChartAxisTick] = []
	steps = 4
	span = max_price - min_price
	for index in range(steps + 1):
		ratio = Decimal(index) / Decimal(steps)
		price = max_price - (span * ratio)
		label_price = _normalize_money(price) or price
		position = top_padding + round((usable_height * index) / steps)
		ticks.append(ChartAxisTick(y=position, label=_format_price_text(label_price, "TRY")))
	return ticks


def _build_x_axis_labels(points: Sequence[ChartPoint]) -> list[ChartAxisLabel]:
	if not points:
		return []
	if len(points) == 1:
		return [ChartAxisLabel(x=points[0].x, label=points[0].recorded_at_label)]
	if len(points) == 2:
		return [
			ChartAxisLabel(x=points[0].x, label=points[0].recorded_at_label),
			ChartAxisLabel(x=points[-1].x, label=points[-1].recorded_at_label),
		]
	middle_index = len(points) // 2
	return [
		ChartAxisLabel(x=points[0].x, label=points[0].recorded_at_label),
		ChartAxisLabel(x=points[middle_index].x, label=points[middle_index].recorded_at_label),
		ChartAxisLabel(x=points[-1].x, label=points[-1].recorded_at_label),
	]


def _format_chart_timestamp(value: datetime) -> str:
	value = _ensure_utc(value)
	return value.strftime("%d %b %H:%M")


def _format_price_text(value: Decimal | None, currency: str) -> str:
	if value is None:
		return "Not available"
	quantized = value.quantize(MONEY_QUANTIZE, rounding=ROUND_HALF_UP)
	formatted = f"{quantized:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
	if currency.upper() == "TRY":
		return f"{formatted} TL"
	return f"{formatted} {currency.upper()}"


def _ensure_optional_utc(value: datetime | None) -> datetime | None:
	if value is None:
		return None
	return _ensure_utc(value)


def _ensure_utc(value: datetime) -> datetime:
	if value.tzinfo is None:
		return value.replace(tzinfo=timezone.utc)
	return value.astimezone(timezone.utc)


def _is_valid_price(value: Decimal | int | float | None) -> bool:
	price = _coerce_decimal(value)
	return price is not None and price > 0


def _calculate_percentage_change(current_price: Decimal, previous_price: Decimal) -> Decimal | None:
	if previous_price <= 0:
		return None
	change = ((current_price - previous_price) / previous_price) * Decimal("100")
	return change.quantize(PERCENT_QUANTIZE, rounding=ROUND_HALF_UP)


def _normalize_money(value: Decimal | int | float | None) -> Decimal | None:
	price = _coerce_decimal(value)
	if price is None:
		return None
	return price.quantize(MONEY_QUANTIZE, rounding=ROUND_HALF_UP)


def _coerce_decimal(value: Decimal | int | float | None) -> Decimal | None:
	if value is None:
		return None
	if isinstance(value, Decimal):
		return value
	return Decimal(str(value))