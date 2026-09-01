"""Sequential batch scraping orchestration with ScrapeRun tracking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.exc import SQLAlchemyError

from database.db import db
from database.models import ScrapeRun, ScrapeRunItem
from services.persistence_service import PersistenceError, save_scraped_product
from services.scraping_service import SCRAPER_CLASSES


RUN_STATUS_RUNNING = "running"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_COMPLETED_WITH_ERRORS = "completed_with_errors"
RUN_STATUS_FAILED = "failed"

ITEM_STATUS_RUNNING = "running"
ITEM_STATUS_SUCCESS = "success"
ITEM_STATUS_FAILED = "failed"
ITEM_STATUS_SKIPPED = "skipped"

DUPLICATE_URL_REASON = "duplicate_url"
UNSUPPORTED_URL_REASON = "unsupported_url"
SCRAPE_FAILED_REASON = "scrape_failed"
PERSISTENCE_FAILED_REASON = "persistence_failed"
INVALID_DATA_REASON = "invalid_data"


@dataclass(slots=True)
class BatchScrapeItemResult:
	"""Returned batch result for one requested URL."""

	url: str
	platform: str | None
	status: str
	failure_reason: str | None
	error_message: str | None
	listing_id: int | None
	product_name: str | None
	current_price: Decimal | None
	currency: str | None
	started_at: datetime
	finished_at: datetime


@dataclass(slots=True)
class BatchScrapeResult:
	"""Summary DTO returned after a batch scrape run."""

	run_id: int
	status: str
	total: int
	successful: int
	failed: int
	skipped: int
	started_at: datetime
	finished_at: datetime
	duration: timedelta
	item_results: list[BatchScrapeItemResult]


def load_batch_urls(file_path: str | Path) -> list[str]:
	"""Load one URL per line while ignoring blanks and comment lines."""
	path = Path(file_path)
	lines = path.read_text(encoding="utf-8-sig").splitlines()
	urls: list[str] = []
	for line in lines:
		stripped = line.strip()
		if not stripped or stripped.startswith("#"):
			continue
		urls.append(stripped)
	return urls


def normalize_batch_url(url: str) -> str:
	"""Normalize a batch URL conservatively for in-run deduplication."""
	trimmed = url.strip()
	if not trimmed:
		return ""

	parts = urlsplit(trimmed)
	if not parts.scheme or not parts.netloc:
		return trimmed

	return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def run_batch(
	urls: Iterable[str],
	platform: str | None = None,
	fetch_strategy: str = "playwright",
	*,
	headed: bool = False,
	debug_parser: bool = False,
	scraper_classes: tuple[type[Any], ...] | None = None,
	on_run_started: Callable[[ScrapeRun], None] | None = None,
	on_item_result: Callable[[int, int, BatchScrapeItemResult], None] | None = None,
) -> BatchScrapeResult:
	"""Scrape a list of URLs sequentially and track the run in the database."""
	resolved_scraper_classes = scraper_classes or SCRAPER_CLASSES
	requested_urls = list(urls)
	normalized_platform = _normalize_platform(platform)
	started_at = _utc_now()
	run = ScrapeRun(
		platform=_determine_run_platform(requested_urls, normalized_platform, resolved_scraper_classes),
		started_at=started_at,
		status=RUN_STATUS_RUNNING,
	)
	db.session.add(run)
	db.session.commit()
	if on_run_started is not None:
		on_run_started(run)

	seen_urls: set[str] = set()
	scraper_cache: dict[str, Any] = {}
	item_results: list[BatchScrapeItemResult] = []

	for raw_url in requested_urls:
		item_started_at = _utc_now()
		normalized_url = normalize_batch_url(raw_url)
		resolved_platform = _resolve_platform_name(normalized_url, normalized_platform, resolved_scraper_classes)
		stored_url = normalized_url or raw_url.strip()

		if not stored_url:
			item_result = _record_terminal_item(
				run,
				url="",
				platform=resolved_platform,
				status=ITEM_STATUS_FAILED,
				failure_reason=UNSUPPORTED_URL_REASON,
				error_message="The URL is empty or invalid.",
				started_at=item_started_at,
			)
			item_results.append(item_result)
			_emit_item_result(on_item_result, item_results, requested_urls, item_result)
			continue

		if normalized_url in seen_urls:
			item_result = _record_terminal_item(
				run,
				url=stored_url,
				platform=resolved_platform,
				status=ITEM_STATUS_SKIPPED,
				failure_reason=DUPLICATE_URL_REASON,
				error_message="The URL was already processed earlier in this batch run.",
				started_at=item_started_at,
			)
			item_results.append(item_result)
			_emit_item_result(on_item_result, item_results, requested_urls, item_result)
			continue

		seen_urls.add(normalized_url)
		item_row = ScrapeRunItem(
			scrape_run=run,
			url=stored_url,
			platform=resolved_platform,
			status=ITEM_STATUS_RUNNING,
			started_at=item_started_at,
		)
		db.session.add(item_row)
		db.session.commit()

		scraper = _resolve_scraper_instance(
			url=stored_url,
			platform=normalized_platform,
			debug_parser=debug_parser,
			scraper_cache=scraper_cache,
			scraper_classes=resolved_scraper_classes,
		)
		if scraper is None:
			item_result = _finalize_item_row(
				item_row,
				status=ITEM_STATUS_FAILED,
				failure_reason=UNSUPPORTED_URL_REASON,
				error_message="No supported scraper is available for this URL.",
			)
			item_results.append(item_result)
			_emit_item_result(on_item_result, item_results, requested_urls, item_result)
			continue

		try:
			scraped_data = scraper.scrape_product(stored_url, fetch_strategy=fetch_strategy, headed=headed)
		except Exception as error:
			db.session.rollback()
			item_result = _finalize_item_row(
				item_row,
				status=ITEM_STATUS_FAILED,
				failure_reason=SCRAPE_FAILED_REASON,
				error_message=str(error),
			)
			item_results.append(item_result)
			_emit_item_result(on_item_result, item_results, requested_urls, item_result)
			continue

		if scraped_data is None:
			item_result = _finalize_item_row(
				item_row,
				status=ITEM_STATUS_FAILED,
				failure_reason=getattr(scraper, "last_failure_reason", None) or SCRAPE_FAILED_REASON,
				error_message=getattr(scraper, "last_failure_details", None) or "The scraper could not produce valid product data.",
			)
			item_results.append(item_result)
			_emit_item_result(on_item_result, item_results, requested_urls, item_result)
			continue

		try:
			persistence_result = save_scraped_product(scraped_data)
		except PersistenceError as error:
			item_result = _finalize_item_row(
				item_row,
				status=ITEM_STATUS_FAILED,
				failure_reason=PERSISTENCE_FAILED_REASON,
				error_message=str(error),
			)
			item_results.append(item_result)
			_emit_item_result(on_item_result, item_results, requested_urls, item_result)
			continue
		except SQLAlchemyError as error:
			db.session.rollback()
			item_result = _finalize_item_row(
				item_row,
				status=ITEM_STATUS_FAILED,
				failure_reason=PERSISTENCE_FAILED_REASON,
				error_message=str(error),
			)
			item_results.append(item_result)
			_emit_item_result(on_item_result, item_results, requested_urls, item_result)
			continue

		if persistence_result is None:
			item_result = _finalize_item_row(
				item_row,
				status=ITEM_STATUS_FAILED,
				failure_reason=INVALID_DATA_REASON,
				error_message="Persistence validation rejected the scraped product data.",
			)
			item_results.append(item_result)
			_emit_item_result(on_item_result, item_results, requested_urls, item_result)
			continue

		item_result = _finalize_item_row(
			item_row,
			status=ITEM_STATUS_SUCCESS,
			failure_reason=None,
			error_message=None,
			listing_id=persistence_result.listing.id,
			platform=scraped_data.platform,
			product_name=scraped_data.product_name,
			current_price=scraped_data.current_price,
			currency=scraped_data.currency,
		)
		item_results.append(item_result)
		_emit_item_result(on_item_result, item_results, requested_urls, item_result)

	finished_at = _utc_now()
	successful = sum(1 for item in item_results if item.status == ITEM_STATUS_SUCCESS)
	failed = sum(1 for item in item_results if item.status == ITEM_STATUS_FAILED)
	skipped = sum(1 for item in item_results if item.status == ITEM_STATUS_SKIPPED)
	run_started_at = _ensure_utc(run.started_at)
	run.finished_at = finished_at
	run.products_found = successful
	run.listings_found = successful
	run.errors_count = failed
	run.status = _determine_run_status(successful=successful, failed=failed, total=len(item_results))
	run.error_message = _build_run_error_message(total=len(item_results), successful=successful, failed=failed)
	db.session.commit()
	run_finished_at = _ensure_utc(run.finished_at)

	return BatchScrapeResult(
		run_id=run.id,
		status=run.status,
		total=len(item_results),
		successful=successful,
		failed=failed,
		skipped=skipped,
		started_at=run_started_at,
		finished_at=run_finished_at,
		duration=run_finished_at - run_started_at,
		item_results=item_results,
	)


def _resolve_scraper_instance(
	*,
	url: str,
	platform: str | None,
	debug_parser: bool,
	scraper_cache: dict[str, Any],
	scraper_classes: tuple[type[Any], ...],
) -> Any | None:
	scraper_class = _resolve_scraper_class(url, platform, scraper_classes)
	if scraper_class is None:
		return None

	cache_key = getattr(scraper_class, "platform", scraper_class.__name__).strip().lower()
	if cache_key not in scraper_cache:
		scraper_cache[cache_key] = scraper_class(debug_parser=debug_parser)
	return scraper_cache[cache_key]


def _resolve_scraper_class(url: str, platform: str | None, scraper_classes: tuple[type[Any], ...]) -> type[Any] | None:
	if platform is not None:
		for scraper_class in scraper_classes:
			if getattr(scraper_class, "platform", "").strip().lower() == platform:
				return scraper_class if scraper_class.is_supported_url(url) else None
		return None

	for scraper_class in scraper_classes:
		if scraper_class.is_supported_url(url):
			return scraper_class
	return None


def _determine_run_platform(
	urls: list[str],
	explicit_platform: str | None,
	scraper_classes: tuple[type[Any], ...],
) -> str:
	if explicit_platform is not None:
		return explicit_platform

	platforms = {
		platform_name
		for url in urls
		for platform_name in [_resolve_platform_name(normalize_batch_url(url), None, scraper_classes)]
		if platform_name is not None
	}
	if len(platforms) == 1:
		return next(iter(platforms))
	return "mixed"


def _resolve_platform_name(url: str, explicit_platform: str | None, scraper_classes: tuple[type[Any], ...]) -> str | None:
	if explicit_platform is not None:
		return explicit_platform
	for scraper_class in scraper_classes:
		if scraper_class.is_supported_url(url):
			return getattr(scraper_class, "platform", None)
	return None


def _record_terminal_item(
	run: ScrapeRun,
	*,
	url: str,
	platform: str | None,
	status: str,
	failure_reason: str | None,
	error_message: str | None,
	started_at: datetime,
) -> BatchScrapeItemResult:
	item_row = ScrapeRunItem(
		scrape_run=run,
		url=url,
		platform=platform,
		status=status,
		failure_reason=failure_reason,
		error_message=error_message,
		started_at=started_at,
		finished_at=_utc_now(),
	)
	db.session.add(item_row)
	db.session.commit()
	return _build_item_result(item_row)


def _finalize_item_row(
	item_row: ScrapeRunItem,
	*,
	status: str,
	failure_reason: str | None,
	error_message: str | None,
	listing_id: int | None = None,
	platform: str | None = None,
	product_name: str | None = None,
	current_price: Decimal | None = None,
	currency: str | None = None,
) -> BatchScrapeItemResult:
	item_row.status = status
	item_row.failure_reason = failure_reason
	item_row.error_message = error_message
	item_row.finished_at = _utc_now()
	if listing_id is not None:
		item_row.listing_id = listing_id
	if platform is not None:
		item_row.platform = platform
	db.session.commit()
	return _build_item_result(
		item_row,
		product_name=product_name,
		current_price=current_price,
		currency=currency,
	)


def _build_item_result(
	item_row: ScrapeRunItem,
	*,
	product_name: str | None = None,
	current_price: Decimal | None = None,
	currency: str | None = None,
) -> BatchScrapeItemResult:
	return BatchScrapeItemResult(
		url=item_row.url,
		platform=item_row.platform,
		status=item_row.status,
		failure_reason=item_row.failure_reason,
		error_message=item_row.error_message,
		listing_id=item_row.listing_id,
		product_name=product_name,
		current_price=current_price,
		currency=currency,
		started_at=_ensure_utc(item_row.started_at),
		finished_at=_ensure_utc(item_row.finished_at or item_row.started_at),
	)


def _emit_item_result(
	callback: Callable[[int, int, BatchScrapeItemResult], None] | None,
	item_results: list[BatchScrapeItemResult],
	requested_urls: list[str],
	item_result: BatchScrapeItemResult,
) -> None:
	if callback is None:
		return
	callback(len(item_results), len(requested_urls), item_result)


def _determine_run_status(*, successful: int, failed: int, total: int) -> str:
	if total == 0:
		return RUN_STATUS_FAILED
	if failed == 0:
		return RUN_STATUS_COMPLETED
	if successful == 0:
		return RUN_STATUS_FAILED
	return RUN_STATUS_COMPLETED_WITH_ERRORS


def _build_run_error_message(*, total: int, successful: int, failed: int) -> str | None:
	if total == 0:
		return "No URLs were provided for the batch run."
	if failed == 0:
		return None
	if successful == 0:
		return f"All {failed} processed batch items failed."
	return f"{failed} of {total} processed batch items failed."


def _normalize_platform(platform: str | None) -> str | None:
	if platform is None:
		return None
	normalized = platform.strip().lower()
	return normalized or None


def _utc_now() -> datetime:
	return datetime.now(timezone.utc)


def _ensure_utc(value: datetime) -> datetime:
	if value.tzinfo is None:
		return value.replace(tzinfo=timezone.utc)
	return value.astimezone(timezone.utc)