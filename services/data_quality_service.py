"""Centralized operational health reporting for tracked watchlist items."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from math import ceil

from flask import current_app
from sqlalchemy import and_, or_

from database.db import db
from database.models import Listing, Product, ScrapeRunItem, WatchlistItem
from services.settings_service import get_setting_value


DEFAULT_STALE_HOURS = 48
DEFAULT_PAGE_SIZE = 25

HEALTHY = "healthy"
NEVER_SCRAPED = "never_scraped"
STALE = "stale"
BLOCKED = "blocked"
PARSE_FAILED = "parse_failed"
PERSISTENCE_FAILED = "persistence_failed"
ROBOTS_DENIED = "robots_denied"
INVALID_DATA = "invalid_data"
UNSUPPORTED_URL = "unsupported_url"
OTHER_FAILURE = "other_failure"

FAILURE_HEALTH_KEYS = {
	"blocked_by_platform": BLOCKED,
	"http_forbidden": BLOCKED,
	"rate_limited": BLOCKED,
	"challenge_detected": BLOCKED,
	"robots_denied": ROBOTS_DENIED,
	"robots_load_failed": ROBOTS_DENIED,
	"parse_failed": PARSE_FAILED,
	"persistence_failed": PERSISTENCE_FAILED,
	"invalid_data": INVALID_DATA,
	"unsupported_url": UNSUPPORTED_URL,
}

FAILURE_MESSAGES = {
	BLOCKED: "Platform blocked browser request",
	ROBOTS_DENIED: "robots.txt denied access",
	PARSE_FAILED: "Product data could not be parsed",
	PERSISTENCE_FAILED: "Persistence failed",
	INVALID_DATA: "Required product data was missing",
	UNSUPPORTED_URL: "URL unsupported",
	OTHER_FAILURE: "Scrape attempt failed",
}

STATUS_OPTIONS = (
	("all", "All health states"),
	(HEALTHY, "Healthy"),
	(STALE, "Stale"),
	(NEVER_SCRAPED, "Never Scraped"),
	(BLOCKED, "Blocked"),
	("failed", "Failed"),
	(ROBOTS_DENIED, "Robots Denied"),
	(PARSE_FAILED, "Parse Failed"),
	(PERSISTENCE_FAILED, "Persistence Failed"),
	(INVALID_DATA, "Invalid Data"),
	(UNSUPPORTED_URL, "Unsupported URL"),
	(OTHER_FAILURE, "Other Failure"),
)
PLATFORM_OPTIONS = (("all", "All platforms"), ("trendyol", "Trendyol"), ("hepsiburada", "Hepsiburada"))
TRACKING_OPTIONS = (("all", "Active and paused"), ("active", "Active"), ("paused", "Paused"))
SORT_OPTIONS = (
	("worst", "Worst status first"),
	("oldest", "Oldest update"),
	("newest", "Newest update"),
	("platform", "Platform"),
	("product", "Product / label"),
)

FAILED_HEALTH_KEYS = {
	PARSE_FAILED,
	PERSISTENCE_FAILED,
	ROBOTS_DENIED,
	INVALID_DATA,
	UNSUPPORTED_URL,
	OTHER_FAILURE,
}


@dataclass(frozen=True, slots=True)
class HealthState:
	"""Final mutually exclusive health state and its presentation metadata."""

	key: str
	label: str
	css_class: str
	failure_message: str | None = None


HEALTH_STATES = {
	HEALTHY: HealthState(HEALTHY, "Healthy", "health-badge--healthy"),
	NEVER_SCRAPED: HealthState(NEVER_SCRAPED, "Never Scraped", "health-badge--never"),
	STALE: HealthState(STALE, "Stale", "health-badge--stale"),
	BLOCKED: HealthState(BLOCKED, "Blocked", "health-badge--blocked", FAILURE_MESSAGES[BLOCKED]),
	PARSE_FAILED: HealthState(PARSE_FAILED, "Parse Failed", "health-badge--failed", FAILURE_MESSAGES[PARSE_FAILED]),
	PERSISTENCE_FAILED: HealthState(PERSISTENCE_FAILED, "Persistence Failed", "health-badge--failed", FAILURE_MESSAGES[PERSISTENCE_FAILED]),
	ROBOTS_DENIED: HealthState(ROBOTS_DENIED, "Robots Denied", "health-badge--failed", FAILURE_MESSAGES[ROBOTS_DENIED]),
	INVALID_DATA: HealthState(INVALID_DATA, "Invalid Data", "health-badge--failed", FAILURE_MESSAGES[INVALID_DATA]),
	UNSUPPORTED_URL: HealthState(UNSUPPORTED_URL, "Unsupported URL", "health-badge--failed", FAILURE_MESSAGES[UNSUPPORTED_URL]),
	OTHER_FAILURE: HealthState(OTHER_FAILURE, "Failed", "health-badge--failed", FAILURE_MESSAGES[OTHER_FAILURE]),
}


@dataclass(slots=True)
class DataQualityAttempt:
	run_id: int
	status: str
	failure_reason: str | None
	failure_message: str | None
	attempted_at: datetime


@dataclass(slots=True)
class DataQualityItem:
	id: int
	platform: str
	url: str
	external_product_id: str | None
	display_name: str | None
	is_active: bool
	last_scraped_at: datetime | None
	last_scrape_status: str | None
	last_failure_reason: str | None
	health: HealthState
	product_id: int | None
	product_name: str | None
	latest_price: Decimal | None
	currency: str | None
	last_successful_at: datetime | None
	recent_attempts: list[DataQualityAttempt]

	@property
	def primary_name(self) -> str:
		return self.display_name or self.product_name or "Tracked product"


@dataclass(frozen=True, slots=True)
class DataQualitySummary:
	total_tracked: int
	active_tracked: int
	healthy: int
	stale: int
	never_scraped: int
	blocked: int
	failed: int


@dataclass(slots=True)
class DataQualityPage:
	items: list[DataQualityItem]
	summary: DataQualitySummary
	status: str
	platform: str
	tracking: str
	search: str
	sort: str
	page: int
	per_page: int
	total_items: int
	total_pages: int
	stale_hours: int
	status_options: tuple[tuple[str, str], ...] = STATUS_OPTIONS
	platform_options: tuple[tuple[str, str], ...] = PLATFORM_OPTIONS
	tracking_options: tuple[tuple[str, str], ...] = TRACKING_OPTIONS
	sort_options: tuple[tuple[str, str], ...] = SORT_OPTIONS

	@property
	def has_previous(self) -> bool:
		return self.page > 1

	@property
	def has_next(self) -> bool:
		return self.page < self.total_pages


def classify_health_state(
	*,
	last_status: str | None,
	last_failure_reason: str | None,
	last_successful_at: datetime | None,
	is_active: bool,
	stale_before: datetime,
) -> HealthState:
	"""Return one primary health state while preserving structured failure categories."""
	normalized_status = (last_status or "").strip().lower()
	if not normalized_status:
		return HEALTH_STATES[NEVER_SCRAPED]
	if normalized_status != "success":
		key = FAILURE_HEALTH_KEYS.get((last_failure_reason or "").strip().lower(), OTHER_FAILURE)
		return HEALTH_STATES[key]
	if is_active and last_successful_at is not None and _ensure_utc(last_successful_at) < stale_before:
		return HEALTH_STATES[STALE]
	return HEALTH_STATES[HEALTHY]


def get_data_quality_summary(*, now: datetime | None = None, stale_hours: int | None = None) -> DataQualitySummary:
	"""Return active-item health counters plus total tracking counts."""
	items = WatchlistItem.query.order_by(WatchlistItem.id.asc()).all()
	health_map = get_watchlist_health_map(items, now=now, stale_hours=stale_hours)
	active_items = [item for item in items if item.is_active]
	keys = [health_map[item.id].key for item in active_items]
	return DataQualitySummary(
		total_tracked=len(items),
		active_tracked=len(active_items),
		healthy=keys.count(HEALTHY),
		stale=keys.count(STALE),
		never_scraped=keys.count(NEVER_SCRAPED),
		blocked=keys.count(BLOCKED),
		failed=sum(key in FAILED_HEALTH_KEYS for key in keys),
	)


def get_watchlist_health_map(
	items: list[WatchlistItem],
	*,
	now: datetime | None = None,
	stale_hours: int | None = None,
) -> dict[int, HealthState]:
	"""Bulk-load attempt history and classify watchlist items without N+1 queries."""
	if not items:
		return {}
	stale_before = _resolve_now(now) - timedelta(hours=_resolve_stale_hours(stale_hours))
	attempts_by_url = _load_attempts_by_url([item.url for item in items])
	return {
		item.id: _classify_item(item, attempts_by_url.get(item.url, []), stale_before)
		for item in items
	}


def get_data_quality_page(
	*,
	status: str | None = None,
	platform: str | None = None,
	tracking: str | None = None,
	search: str | None = None,
	sort: str | None = None,
	page: int = 1,
	per_page: int | None = None,
	now: datetime | None = None,
) -> DataQualityPage:
	"""Return validated, filtered, sorted, and paginated operational health rows."""
	selected_status = _validated_choice(status, STATUS_OPTIONS, "all")
	selected_platform = _validated_choice(platform, PLATFORM_OPTIONS, "all")
	selected_tracking = _validated_choice(tracking, TRACKING_OPTIONS, "all")
	selected_sort = _validated_choice(sort, SORT_OPTIONS, "worst")
	clean_search = (search or "").strip()[:255]
	page_size = max(1, int(per_page or current_app.config.get("DATA_QUALITY_PAGE_SIZE", DEFAULT_PAGE_SIZE)))
	stale_hours = _resolve_stale_hours(None)
	stale_before = _resolve_now(now) - timedelta(hours=stale_hours)

	query = WatchlistItem.query
	if selected_platform != "all":
		query = query.filter(WatchlistItem.platform == selected_platform)
	if selected_tracking == "active":
		query = query.filter(WatchlistItem.is_active.is_(True))
	elif selected_tracking == "paused":
		query = query.filter(WatchlistItem.is_active.is_(False))
	watchlist_items = query.order_by(WatchlistItem.id.asc()).all()
	attempts_by_url = _load_attempts_by_url([item.url for item in watchlist_items])
	listing_map = _load_listing_map(watchlist_items)
	rows = [
		_build_page_item(item, attempts_by_url.get(item.url, []), listing_map.get(item.id), stale_before)
		for item in watchlist_items
	]
	if clean_search:
		rows = [row for row in rows if _matches_search(row, clean_search)]
	rows = [row for row in rows if _matches_status(row.health.key, selected_status)]
	rows.sort(key=lambda item: _sort_key(item, selected_sort))

	total_items = len(rows)
	total_pages = max(1, ceil(total_items / page_size))
	validated_page = min(max(1, int(page or 1)), total_pages)
	start = (validated_page - 1) * page_size
	page_items = rows[start:start + page_size]

	return DataQualityPage(
		items=page_items,
		summary=get_data_quality_summary(now=now, stale_hours=stale_hours),
		status=selected_status,
		platform=selected_platform,
		tracking=selected_tracking,
		search=clean_search,
		sort=selected_sort,
		page=validated_page,
		per_page=page_size,
		total_items=total_items,
		total_pages=total_pages,
		stale_hours=stale_hours,
	)


def failure_message(reason: str | None) -> str | None:
	"""Return a concise safe UI message for a structured failure reason."""
	if reason is None:
		return None
	key = FAILURE_HEALTH_KEYS.get(reason.strip().lower(), OTHER_FAILURE)
	return FAILURE_MESSAGES[key]


def _classify_item(item: WatchlistItem, attempts: list[ScrapeRunItem], stale_before: datetime) -> HealthState:
	latest_attempt = attempts[0] if attempts else None
	last_status = latest_attempt.status if latest_attempt is not None else item.last_scrape_status
	last_reason = latest_attempt.failure_reason if latest_attempt is not None else item.last_failure_reason
	last_successful_at = _last_successful_at(attempts)
	if last_successful_at is None and item.last_scrape_status == "success":
		last_successful_at = item.last_scraped_at
	return classify_health_state(
		last_status=last_status,
		last_failure_reason=last_reason,
		last_successful_at=last_successful_at,
		is_active=item.is_active,
		stale_before=stale_before,
	)


def _build_page_item(
	item: WatchlistItem,
	attempts: list[ScrapeRunItem],
	listing: tuple[Listing, Product] | None,
	stale_before: datetime,
) -> DataQualityItem:
	latest_attempt = attempts[0] if attempts else None
	last_status = latest_attempt.status if latest_attempt is not None else item.last_scrape_status
	last_reason = latest_attempt.failure_reason if latest_attempt is not None else item.last_failure_reason
	last_scraped_at = _attempt_time(latest_attempt) if latest_attempt is not None else item.last_scraped_at
	last_successful_at = _last_successful_at(attempts)
	if last_successful_at is None and item.last_scrape_status == "success":
		last_successful_at = item.last_scraped_at
	health = classify_health_state(
		last_status=last_status,
		last_failure_reason=last_reason,
		last_successful_at=last_successful_at,
		is_active=item.is_active,
		stale_before=stale_before,
	)
	return DataQualityItem(
		id=item.id,
		platform=item.platform,
		url=item.url,
		external_product_id=item.external_product_id,
		display_name=item.display_name,
		is_active=item.is_active,
		last_scraped_at=last_scraped_at,
		last_scrape_status=last_status,
		last_failure_reason=last_reason,
		health=health,
		product_id=listing[1].id if listing else None,
		product_name=listing[1].name if listing else None,
		latest_price=listing[0].current_price if listing else None,
		currency=listing[0].currency if listing else None,
		last_successful_at=last_successful_at,
		recent_attempts=[
			DataQualityAttempt(
				run_id=attempt.scrape_run_id,
				status=attempt.status,
				failure_reason=attempt.failure_reason,
				failure_message=failure_message(attempt.failure_reason),
				attempted_at=_attempt_time(attempt),
			)
			for attempt in attempts[:5]
		],
	)


def _load_attempts_by_url(urls: list[str]) -> dict[str, list[ScrapeRunItem]]:
	if not urls:
		return {}
	attempt_time = db.func.coalesce(ScrapeRunItem.finished_at, ScrapeRunItem.started_at)
	rows = (
		ScrapeRunItem.query
		.filter(ScrapeRunItem.url.in_(urls))
		.order_by(ScrapeRunItem.url.asc(), attempt_time.desc(), ScrapeRunItem.id.desc())
		.all()
	)
	result: dict[str, list[ScrapeRunItem]] = {}
	for row in rows:
		result.setdefault(row.url, []).append(row)
	return result


def _load_listing_map(items: list[WatchlistItem]) -> dict[int, tuple[Listing, Product]]:
	if not items:
		return {}
	filters = [Listing.url.in_([item.url for item in items])]
	keys = [
		and_(Listing.platform == item.platform, Listing.external_product_id == item.external_product_id)
		for item in items
		if item.external_product_id
	]
	if keys:
		filters.append(or_(*keys))
	rows = (
		db.session.query(Listing, Product)
		.join(Product, Listing.product_id == Product.id)
		.filter(or_(*filters))
		.order_by(Listing.last_scraped_at.desc(), Listing.updated_at.desc(), Listing.id.desc())
		.all()
	)
	by_url: dict[str, tuple[Listing, Product]] = {}
	by_key: dict[tuple[str, str], tuple[Listing, Product]] = {}
	for listing, product in rows:
		by_url.setdefault(listing.url, (listing, product))
		if listing.external_product_id:
			by_key.setdefault((listing.platform, listing.external_product_id), (listing, product))
	return {
		item.id: by_url.get(item.url) or by_key.get((item.platform, item.external_product_id))
		for item in items
		if by_url.get(item.url) or (item.external_product_id and by_key.get((item.platform, item.external_product_id)))
	}


def _last_successful_at(attempts: list[ScrapeRunItem]) -> datetime | None:
	for attempt in attempts:
		if attempt.status == "success":
			return _attempt_time(attempt)
	return None


def _attempt_time(attempt: ScrapeRunItem) -> datetime:
	return _ensure_utc(attempt.finished_at or attempt.started_at)


def _matches_status(health_key: str, selected_status: str) -> bool:
	if selected_status == "all":
		return True
	if selected_status == "failed":
		return health_key in FAILED_HEALTH_KEYS
	return health_key == selected_status


def _matches_search(item: DataQualityItem, search: str) -> bool:
	needle = search.casefold()
	values = (item.display_name, item.product_name, item.url, item.external_product_id)
	return any(needle in value.casefold() for value in values if value)


def _sort_key(item: DataQualityItem, selected_sort: str) -> tuple:
	oldest_default = datetime.min.replace(tzinfo=timezone.utc)
	newest_default = datetime.max.replace(tzinfo=timezone.utc)
	last_update = _ensure_utc(item.last_scraped_at) if item.last_scraped_at else None
	name = item.primary_name.casefold()
	if selected_sort == "oldest":
		return (last_update or oldest_default, item.id)
	if selected_sort == "newest":
		return (0 if last_update else 1, -last_update.timestamp() if last_update else 0, item.id)
	if selected_sort == "platform":
		return (item.platform.casefold(), name, item.id)
	if selected_sort == "product":
		return (name, item.platform.casefold(), item.id)
	worst_rank = {
		BLOCKED: 0,
		PERSISTENCE_FAILED: 1,
		ROBOTS_DENIED: 2,
		PARSE_FAILED: 3,
		INVALID_DATA: 4,
		OTHER_FAILURE: 5,
		UNSUPPORTED_URL: 6,
		STALE: 7,
		NEVER_SCRAPED: 8,
		HEALTHY: 9,
	}
	return (worst_rank[item.health.key], last_update or newest_default, name, item.id)


def _validated_choice(value: str | None, options: tuple[tuple[str, str], ...], default: str) -> str:
	allowed = {key for key, _label in options}
	normalized = (value or default).strip().lower()
	return normalized if normalized in allowed else default


def _resolve_stale_hours(value: int | None) -> int:
	configured = value if value is not None else get_setting_value("data_quality_stale_hours")
	try:
		return max(1, int(configured))
	except (TypeError, ValueError):
		return DEFAULT_STALE_HOURS


def _resolve_now(value: datetime | None) -> datetime:
	return _ensure_utc(value or datetime.now(timezone.utc))


def _ensure_utc(value: datetime) -> datetime:
	if value.tzinfo is None:
		return value.replace(tzinfo=timezone.utc)
	return value.astimezone(timezone.utc)
