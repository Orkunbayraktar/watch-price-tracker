"""Service-layer orchestration and view data for the Scraping Control Center."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from database.db import db
from database.models import Listing, Product, ScrapeRunItem, WatchlistItem
from services.batch_scraping_service import BatchScrapeResult, run_batch
from services.data_quality_service import failure_message
from services.scrape_run_service import (
	ScrapeRunSummary,
	get_recent_scrape_run_summaries,
	get_running_scrape_run_summary,
)
from services.scheduler_service import SchedulerSummary, get_scheduler_summary
from services.watchlist_service import (
	DEFAULT_WATCHLIST_FETCH_STRATEGY,
	WatchlistPageItem,
	list_watchlist_items,
	update_active_watchlist_items,
	update_selected_watchlist_items,
	validate_and_normalize_watchlist_url,
)


RECENT_RUN_LIMIT = 10
RECENT_FAILURE_LIMIT = 10


class ScrapingControlError(RuntimeError):
	"""Base error for Control Center operations."""


class ScrapingControlValidationError(ScrapingControlError):
	"""Raised when a Control Center selection is invalid."""


class ScrapingRunInProgressError(ScrapingControlError):
	"""Raised when another run is already marked running."""


@dataclass(frozen=True, slots=True)
class RecentScrapingFailure:
	run_id: int
	product_label: str
	platform: str | None
	url: str
	failure_reason: str
	failure_message: str
	failed_at: datetime


@dataclass(frozen=True, slots=True)
class ScrapingActionFailure:
	url: str
	platform: str | None
	failure_reason: str
	failure_message: str


@dataclass(slots=True)
class ScrapingActionResult:
	kind: str
	title: str
	batch_result: BatchScrapeResult | None
	skipped_paused: int = 0
	skipped_missing: int = 0
	failed_items: list[ScrapingActionFailure] | None = None

	@property
	def single_item(self):
		if self.batch_result is None or not self.batch_result.item_results:
			return None
		return self.batch_result.item_results[0]


@dataclass(slots=True)
class ScrapingControlData:
	watchlist_items: list[WatchlistPageItem]
	active_count: int
	paused_count: int
	latest_run: ScrapeRunSummary | None
	running_run: ScrapeRunSummary | None
	recent_runs: list[ScrapeRunSummary]
	recent_failures: list[RecentScrapingFailure]
	scheduler_summary: SchedulerSummary


def get_scraping_control_data() -> ScrapingControlData:
	"""Compose Control Center data from existing watchlist and run services."""
	watchlist_items = list_watchlist_items()
	recent_runs = get_recent_scrape_run_summaries(limit=RECENT_RUN_LIMIT)
	return ScrapingControlData(
		watchlist_items=watchlist_items,
		active_count=sum(item.is_active for item in watchlist_items),
		paused_count=sum(not item.is_active for item in watchlist_items),
		latest_run=recent_runs[0] if recent_runs else None,
		running_run=get_running_scrape_run_summary(),
		recent_runs=recent_runs,
		recent_failures=_get_recent_failures(limit=RECENT_FAILURE_LIMIT),
		scheduler_summary=get_scheduler_summary(),
	)


def update_all_active_products() -> ScrapingActionResult:
	"""Update all active watchlist products through the existing watchlist service."""
	_ensure_no_running_run()
	batch_result = update_active_watchlist_items()
	return _build_action_result("active", "Active Products Update", batch_result)


def update_selected_products(raw_item_ids: list[str | int]) -> ScrapingActionResult:
	"""Validate a selection, skip paused rows, and update selected active products."""
	item_ids = _normalize_item_ids(raw_item_ids)
	if not item_ids:
		raise ScrapingControlValidationError("Select at least one active product to update.")

	items = WatchlistItem.query.filter(WatchlistItem.id.in_(item_ids)).all()
	active_ids = [item.id for item in items if item.is_active]
	skipped_paused = sum(not item.is_active for item in items)
	skipped_missing = len(item_ids) - len(items)
	if not active_ids:
		return ScrapingActionResult(
			kind="selected",
			title="Selected Products Update",
			batch_result=None,
			skipped_paused=skipped_paused,
			skipped_missing=skipped_missing,
			failed_items=[],
		)

	_ensure_no_running_run()
	batch_result = update_selected_watchlist_items(active_ids)
	return _build_action_result(
		"selected",
		"Selected Products Update",
		batch_result,
		skipped_paused=skipped_paused,
		skipped_missing=skipped_missing,
	)


def scrape_one_product(url: str) -> ScrapingActionResult:
	"""Validate and scrape one URL through the existing tracked batch workflow."""
	canonical_url = validate_and_normalize_watchlist_url(url)
	_ensure_no_running_run()
	batch_result = run_batch(
		[canonical_url],
		fetch_strategy=DEFAULT_WATCHLIST_FETCH_STRATEGY,
	)
	return _build_action_result("single", "Single Product Scrape", batch_result)


def _ensure_no_running_run() -> None:
	running_run = get_running_scrape_run_summary()
	if running_run is not None:
		raise ScrapingRunInProgressError(
			f"A scraping run is already in progress (run ID {running_run.run_id})."
		)


def _normalize_item_ids(raw_item_ids: list[str | int]) -> list[int]:
	item_ids: list[int] = []
	seen: set[int] = set()
	for raw_item_id in raw_item_ids:
		try:
			item_id = int(raw_item_id)
		except (TypeError, ValueError):
			continue
		if item_id <= 0 or item_id in seen:
			continue
		seen.add(item_id)
		item_ids.append(item_id)
	return item_ids


def _build_action_result(
	kind: str,
	title: str,
	batch_result: BatchScrapeResult | None,
	*,
	skipped_paused: int = 0,
	skipped_missing: int = 0,
) -> ScrapingActionResult:
	failed_items = []
	if batch_result is not None:
		failed_items = [
			ScrapingActionFailure(
				url=item.url,
				platform=item.platform,
				failure_reason=item.failure_reason or "other_failure",
				failure_message=failure_message(item.failure_reason) or "Scrape attempt failed",
			)
			for item in batch_result.item_results
			if item.status == "failed"
		]
	return ScrapingActionResult(
		kind=kind,
		title=title,
		batch_result=batch_result,
		skipped_paused=skipped_paused,
		skipped_missing=skipped_missing,
		failed_items=failed_items,
	)


def _get_recent_failures(limit: int) -> list[RecentScrapingFailure]:
	attempt_time = db.func.coalesce(ScrapeRunItem.finished_at, ScrapeRunItem.started_at)
	rows = (
		db.session.query(
			ScrapeRunItem.scrape_run_id.label("run_id"),
			ScrapeRunItem.url.label("url"),
			ScrapeRunItem.platform.label("platform"),
			ScrapeRunItem.failure_reason.label("failure_reason"),
			attempt_time.label("failed_at"),
			WatchlistItem.display_name.label("display_name"),
			Product.name.label("product_name"),
		)
		.outerjoin(WatchlistItem, WatchlistItem.url == ScrapeRunItem.url)
		.outerjoin(Listing, Listing.id == ScrapeRunItem.listing_id)
		.outerjoin(Product, Product.id == Listing.product_id)
		.filter(ScrapeRunItem.status == "failed")
		.order_by(attempt_time.desc(), ScrapeRunItem.id.desc())
		.limit(limit)
		.all()
	)
	return [
		RecentScrapingFailure(
			run_id=row.run_id,
			product_label=row.display_name or row.product_name or "Manual product URL",
			platform=row.platform,
			url=row.url,
			failure_reason=row.failure_reason or "other_failure",
			failure_message=failure_message(row.failure_reason) or "Scrape attempt failed",
			failed_at=_ensure_utc(row.failed_at),
		)
		for row in rows
	]


def _ensure_utc(value: datetime) -> datetime:
	if value.tzinfo is None:
		return value.replace(tzinfo=timezone.utc)
	return value.astimezone(timezone.utc)
