"""Business logic for tracked marketplace watchlist items."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlsplit

from sqlalchemy import and_, or_

from database.db import db
from database.models import Listing, Product, Seller, WatchlistItem
from services.batch_scraping_service import BatchScrapeResult, run_batch
from services.data_quality_service import HealthState, get_watchlist_health_map
from services.scraping_service import extract_external_product_id_for_url, get_platform_for_url
from services.url_service import normalize_tracking_url


DEFAULT_WATCHLIST_FETCH_STRATEGY = "playwright"


class WatchlistServiceError(RuntimeError):
	"""Base error raised for watchlist operations."""


class WatchlistValidationError(WatchlistServiceError):
	"""Raised when user-submitted watchlist data is invalid."""


@dataclass(slots=True)
class WatchlistLinkedListing:
	"""Optional latest known listing information for a tracked URL."""

	listing_id: int
	product_id: int
	product_name: str
	seller_name: str | None
	current_price: Decimal
	currency: str
	last_updated: datetime | None


@dataclass(slots=True)
class WatchlistPageItem:
	"""View-friendly representation of one tracked URL."""

	id: int
	platform: str
	url: str
	external_product_id: str | None
	display_name: str | None
	is_active: bool
	created_at: datetime
	updated_at: datetime
	last_scraped_at: datetime | None
	last_scrape_status: str | None
	last_failure_reason: str | None
	linked_listing: WatchlistLinkedListing | None
	health: HealthState


def list_watchlist_items() -> list[WatchlistPageItem]:
	"""Return all tracked URLs with optional latest listing context."""
	items = (
		WatchlistItem.query
		.order_by(WatchlistItem.is_active.desc(), WatchlistItem.created_at.desc(), WatchlistItem.id.desc())
		.all()
	)
	linked_listing_map = _load_linked_listing_map(items)
	health_map = get_watchlist_health_map(items)
	return [
		WatchlistPageItem(
			id=item.id,
			platform=item.platform,
			url=item.url,
			external_product_id=item.external_product_id,
			display_name=item.display_name,
			is_active=item.is_active,
			created_at=item.created_at,
			updated_at=item.updated_at,
			last_scraped_at=item.last_scraped_at,
			last_scrape_status=item.last_scrape_status,
			last_failure_reason=item.last_failure_reason,
			linked_listing=linked_listing_map.get(item.id),
			health=health_map[item.id],
		)
		for item in items
	]


def get_watchlist_item(item_id: int) -> WatchlistItem | None:
	"""Return one tracked item by its primary key."""
	return db.session.get(WatchlistItem, item_id)


def create_watchlist_item(url: str, display_name: str | None = None) -> WatchlistItem:
	"""Validate and persist a new tracked product URL."""
	canonical_url = validate_and_normalize_watchlist_url(url)
	if WatchlistItem.query.filter_by(url=canonical_url).first() is not None:
		raise WatchlistValidationError("This product URL is already being tracked.")

	platform = get_platform_for_url(canonical_url)
	if platform is None:
		raise WatchlistValidationError("Only Trendyol and Hepsiburada product URLs are supported.")

	item = WatchlistItem(
		platform=platform,
		url=canonical_url,
		external_product_id=extract_external_product_id_for_url(canonical_url),
		display_name=_clean_display_name(display_name),
		is_active=True,
	)
	db.session.add(item)
	db.session.commit()
	return item


def delete_watchlist_item(item_id: int) -> WatchlistItem | None:
	"""Delete a tracked URL without touching product or listing history."""
	item = get_watchlist_item(item_id)
	if item is None:
		return None
	db.session.delete(item)
	db.session.commit()
	return item


def set_watchlist_item_active(item_id: int, is_active: bool) -> WatchlistItem | None:
	"""Activate or pause a tracked URL."""
	item = get_watchlist_item(item_id)
	if item is None:
		return None
	item.is_active = bool(is_active)
	db.session.commit()
	return item


def prepare_active_urls_for_batch() -> list[str]:
	"""Return active tracked URLs in a stable processing order."""
	rows = (
		WatchlistItem.query
		.filter(WatchlistItem.is_active.is_(True))
		.order_by(WatchlistItem.created_at.asc(), WatchlistItem.id.asc())
		.all()
	)
	return [row.url for row in rows]


def update_last_scrape_metadata(
	item_id: int,
	*,
	last_scraped_at: datetime | None,
	last_scrape_status: str | None,
	last_failure_reason: str | None,
) -> WatchlistItem | None:
	"""Persist the latest scrape outcome for one watchlist item."""
	item = get_watchlist_item(item_id)
	if item is None:
		return None
	item.last_scraped_at = last_scraped_at
	item.last_scrape_status = last_scrape_status
	item.last_failure_reason = last_failure_reason
	db.session.commit()
	return item


def update_active_watchlist_items(
	*,
	fetch_strategy: str = DEFAULT_WATCHLIST_FETCH_STRATEGY,
	headed: bool = False,
	debug_parser: bool = False,
) -> BatchScrapeResult | None:
	"""Run a synchronous batch update for active tracked URLs."""
	active_items = (
		WatchlistItem.query
		.filter(WatchlistItem.is_active.is_(True))
		.order_by(WatchlistItem.created_at.asc(), WatchlistItem.id.asc())
		.all()
	)
	if not active_items:
		return None
	return _run_watchlist_batch(
		active_items,
		fetch_strategy=fetch_strategy,
		headed=headed,
		debug_parser=debug_parser,
	)


def update_selected_watchlist_items(
	item_ids: list[int],
	*,
	fetch_strategy: str = DEFAULT_WATCHLIST_FETCH_STRATEGY,
	headed: bool = False,
	debug_parser: bool = False,
) -> BatchScrapeResult | None:
	"""Update selected active watchlist items through the shared batch path."""
	if not item_ids:
		return None
	items = (
		WatchlistItem.query
		.filter(WatchlistItem.id.in_(item_ids), WatchlistItem.is_active.is_(True))
		.order_by(WatchlistItem.created_at.asc(), WatchlistItem.id.asc())
		.all()
	)
	if not items:
		return None
	return _run_watchlist_batch(
		items,
		fetch_strategy=fetch_strategy,
		headed=headed,
		debug_parser=debug_parser,
	)


def update_watchlist_item(
	item_id: int,
	*,
	fetch_strategy: str = DEFAULT_WATCHLIST_FETCH_STRATEGY,
	headed: bool = False,
	debug_parser: bool = False,
) -> BatchScrapeResult:
	"""Retry one active tracked URL through the existing batch workflow."""
	item = get_watchlist_item(item_id)
	if item is None:
		raise WatchlistValidationError("The watchlist item no longer exists.")
	if not item.is_active:
		raise WatchlistValidationError("Activate this watchlist item before retrying it.")

	return _run_watchlist_batch(
		[item],
		fetch_strategy=fetch_strategy,
		headed=headed,
		debug_parser=debug_parser,
	)


def validate_and_normalize_watchlist_url(url: str) -> str:
	"""Validate and normalize one supported marketplace product URL."""
	normalized_url = normalize_tracking_url(url)
	if not normalized_url:
		raise WatchlistValidationError("Enter a Trendyol or Hepsiburada product URL.")

	parts = urlsplit(normalized_url)
	if parts.scheme.lower() in {"javascript", "file"}:
		raise WatchlistValidationError("Only HTTP and HTTPS marketplace product URLs are allowed.")
	if parts.scheme.lower() not in {"http", "https"}:
		raise WatchlistValidationError("Enter a valid HTTP or HTTPS marketplace product URL.")
	if not parts.netloc:
		raise WatchlistValidationError("Enter a valid HTTP or HTTPS marketplace product URL.")

	platform = get_platform_for_url(normalized_url)
	if platform is None:
		raise WatchlistValidationError("Only supported Trendyol and Hepsiburada product URLs can be tracked.")

	return normalized_url


def _run_watchlist_batch(
	items: list[WatchlistItem],
	*,
	fetch_strategy: str,
	headed: bool,
	debug_parser: bool,
) -> BatchScrapeResult:
	item_ids_by_url = {item.url: item.id for item in items}

	def handle_item_result(_index: int, _total: int, item_result) -> None:
		item_id = item_ids_by_url.get(item_result.url)
		if item_id is None:
			return
		update_last_scrape_metadata(
			item_id,
			last_scraped_at=item_result.finished_at,
			last_scrape_status=item_result.status,
			last_failure_reason=None if item_result.status == "success" else item_result.failure_reason,
		)

	return run_batch(
		[item.url for item in items],
		fetch_strategy=fetch_strategy,
		headed=headed,
		debug_parser=debug_parser,
		on_item_result=handle_item_result,
	)


def _clean_display_name(value: str | None) -> str | None:
	if value is None:
		return None
	cleaned = value.strip()
	return cleaned or None


def _load_linked_listing_map(items: list[WatchlistItem]) -> dict[int, WatchlistLinkedListing]:
	if not items:
		return {}

	urls = [item.url for item in items]
	external_key_filters = [
		and_(Listing.platform == item.platform, Listing.external_product_id == item.external_product_id)
		for item in items
		if item.external_product_id
	]
	filters = [Listing.url.in_(urls)]
	if external_key_filters:
		filters.append(or_(*external_key_filters))

	rows = (
		db.session.query(Listing, Product, Seller)
		.join(Product, Listing.product_id == Product.id)
		.outerjoin(Seller, Listing.seller_id == Seller.id)
		.filter(or_(*filters))
		.all()
	)

	latest_by_url: dict[str, WatchlistLinkedListing] = {}
	latest_by_key: dict[tuple[str, str], WatchlistLinkedListing] = {}

	for listing, product, seller in rows:
		linked_listing = WatchlistLinkedListing(
			listing_id=listing.id,
			product_id=product.id,
			product_name=product.name,
			seller_name=None if seller is None else seller.name,
			current_price=listing.current_price,
			currency=listing.currency,
			last_updated=_listing_last_updated(listing),
		)
		latest_by_url[listing.url] = _choose_newer_linked_listing(latest_by_url.get(listing.url), linked_listing)
		if listing.external_product_id:
			key = (listing.platform, listing.external_product_id)
			latest_by_key[key] = _choose_newer_linked_listing(latest_by_key.get(key), linked_listing)

	return {
		item.id: latest_by_url.get(item.url) or (
			latest_by_key.get((item.platform, item.external_product_id))
			if item.external_product_id is not None
			else None
		)
		for item in items
	}


def _choose_newer_linked_listing(
	current: WatchlistLinkedListing | None,
	candidate: WatchlistLinkedListing,
) -> WatchlistLinkedListing:
	if current is None:
		return candidate
	if _linked_listing_sort_key(candidate.last_updated) >= _linked_listing_sort_key(current.last_updated):
		return candidate
	return current


def _listing_last_updated(listing: Listing) -> datetime | None:
	return listing.last_scraped_at or listing.updated_at or listing.created_at


def _linked_listing_sort_key(value: datetime | None) -> float:
	if value is None:
		return float("-inf")
	if value.tzinfo is None:
		value = value.replace(tzinfo=timezone.utc)
	else:
		value = value.astimezone(timezone.utc)
	return value.timestamp()
