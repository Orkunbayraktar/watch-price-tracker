"""Tests for centralized watchlist health classification and retry behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import create_app
from database.db import db, initialize_database
from database.models import ScrapeRun, ScrapeRunItem, WatchlistItem
from services.batch_scraping_service import BatchScrapeItemResult, BatchScrapeResult
from services.data_quality_service import (
	BLOCKED,
	HEALTHY,
	INVALID_DATA,
	NEVER_SCRAPED,
	PARSE_FAILED,
	PERSISTENCE_FAILED,
	ROBOTS_DENIED,
	STALE,
	get_data_quality_page,
	get_data_quality_summary,
	get_watchlist_health_map,
)
from services.watchlist_service import DEFAULT_WATCHLIST_FETCH_STRATEGY, update_watchlist_item


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def app_context(tmp_path: Path):
	app = create_app(
		{
			"TESTING": True,
			"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
			"IMPORT_STATE_DIR": tmp_path / "import-state",
			"DATA_QUALITY_STALE_HOURS": 48,
			"DATA_QUALITY_PAGE_SIZE": 25,
		}
	)
	context = app.app_context()
	context.push()
	initialize_database(app)
	try:
		yield app
	finally:
		db.session.remove()
		db.drop_all()
		db.engine.dispose()
		context.pop()


def add_watchlist_item(
	name: str,
	*,
	platform: str = "trendyol",
	is_active: bool = True,
	url_suffix: str | None = None,
) -> WatchlistItem:
	suffix = url_suffix or name.lower().replace(" ", "-")
	item = WatchlistItem(
		platform=platform,
		url=f"https://www.{platform}.com/{suffix}-p-{abs(hash(suffix)) % 100000}",
		external_product_id=str(abs(hash(suffix)) % 100000),
		display_name=name,
		is_active=is_active,
	)
	db.session.add(item)
	db.session.commit()
	return item


def add_attempt(
	item: WatchlistItem,
	*,
	status: str,
	at: datetime,
	failure_reason: str | None = None,
) -> ScrapeRunItem:
	run = ScrapeRun(
		platform=item.platform,
		started_at=at,
		finished_at=at + timedelta(minutes=1),
		status="completed" if status == "success" else "failed",
		products_found=1 if status == "success" else 0,
		listings_found=1 if status == "success" else 0,
		errors_count=0 if status == "success" else 1,
	)
	attempt = ScrapeRunItem(
		scrape_run=run,
		url=item.url,
		platform=item.platform,
		status=status,
		failure_reason=failure_reason,
		started_at=at,
		finished_at=at + timedelta(minutes=1),
	)
	db.session.add_all([run, attempt])
	db.session.commit()
	return attempt


@pytest.mark.parametrize(
	("status", "reason", "age_hours", "expected"),
	[
		("success", None, 2, HEALTHY),
		("failed", "blocked_by_platform", 2, BLOCKED),
		("failed", "robots_denied", 2, ROBOTS_DENIED),
		("failed", "invalid_data", 2, INVALID_DATA),
		("failed", "parse_failed", 2, PARSE_FAILED),
		("failed", "persistence_failed", 2, PERSISTENCE_FAILED),
	],
)
def test_structured_attempts_map_to_distinct_health_states(
	app_context,
	status: str,
	reason: str | None,
	age_hours: int,
	expected: str,
) -> None:
	item = add_watchlist_item(expected)
	add_attempt(item, status=status, failure_reason=reason, at=NOW - timedelta(hours=age_hours))

	health = get_watchlist_health_map([item], now=NOW)[item.id]

	assert health.key == expected


def test_never_scraped_is_separate_from_stale(app_context) -> None:
	item = add_watchlist_item("Never attempted")

	assert get_watchlist_health_map([item], now=NOW)[item.id].key == NEVER_SCRAPED


def test_old_successful_active_item_is_stale(app_context) -> None:
	item = add_watchlist_item("Old success")
	add_attempt(item, status="success", at=NOW - timedelta(hours=49))

	assert get_watchlist_health_map([item], now=NOW)[item.id].key == STALE


def test_paused_old_success_is_not_marked_stale(app_context) -> None:
	item = add_watchlist_item("Paused success", is_active=False)
	add_attempt(item, status="success", at=NOW - timedelta(days=10))

	assert get_watchlist_health_map([item], now=NOW)[item.id].key == HEALTHY


def test_summary_counts_active_primary_states_without_counting_paused_items(app_context) -> None:
	healthy = add_watchlist_item("Healthy")
	add_attempt(healthy, status="success", at=NOW - timedelta(hours=1))
	stale = add_watchlist_item("Stale")
	add_attempt(stale, status="success", at=NOW - timedelta(hours=50))
	add_watchlist_item("Never")
	blocked = add_watchlist_item("Blocked")
	add_attempt(blocked, status="failed", failure_reason="http_forbidden", at=NOW)
	failed = add_watchlist_item("Parser")
	add_attempt(failed, status="failed", failure_reason="parse_failed", at=NOW)
	paused = add_watchlist_item("Paused", is_active=False)
	add_attempt(paused, status="failed", failure_reason="invalid_data", at=NOW)

	summary = get_data_quality_summary(now=NOW)

	assert summary.total_tracked == 6
	assert summary.active_tracked == 5
	assert summary.healthy == 1
	assert summary.stale == 1
	assert summary.never_scraped == 1
	assert summary.blocked == 1
	assert summary.failed == 1


def test_page_filters_and_validates_control_values(app_context) -> None:
	trendyol = add_watchlist_item("Trendyol blocked")
	add_attempt(trendyol, status="failed", failure_reason="blocked_by_platform", at=NOW)
	add_watchlist_item("Paused Hepsiburada", platform="hepsiburada", is_active=False)

	filtered = get_data_quality_page(status="blocked", platform="trendyol", tracking="active", now=NOW)
	validated = get_data_quality_page(status="not-real", platform="unsafe", tracking="unknown", sort="DROP TABLE", now=NOW)

	assert [item.display_name for item in filtered.items] == ["Trendyol blocked"]
	assert validated.status == "all"
	assert validated.platform == "all"
	assert validated.tracking == "all"
	assert validated.sort == "worst"


def test_page_sorting_and_pagination_are_backend_controlled(app_context) -> None:
	for index in range(27):
		add_watchlist_item(f"Product {index:02d}", url_suffix=f"quality-{index}")

	page = get_data_quality_page(sort="product", page=2, per_page=25, now=NOW)

	assert page.total_items == 27
	assert page.total_pages == 2
	assert [item.display_name for item in page.items] == ["Product 25", "Product 26"]


def test_single_retry_reuses_batch_service_and_defaults_to_playwright(app_context, monkeypatch: pytest.MonkeyPatch) -> None:
	item = add_watchlist_item("Retry me", platform="hepsiburada")
	started_at = NOW
	finished_at = NOW + timedelta(minutes=1)

	def fake_run_batch(urls, fetch_strategy="requests", **kwargs):
		assert urls == [item.url]
		assert fetch_strategy == DEFAULT_WATCHLIST_FETCH_STRATEGY == "playwright"
		result_item = BatchScrapeItemResult(
			url=item.url,
			platform=item.platform,
			status="failed",
			failure_reason="blocked_by_platform",
			error_message="Platform blocked the request.",
			listing_id=None,
			product_name=None,
			current_price=None,
			currency=None,
			started_at=started_at,
			finished_at=finished_at,
		)
		kwargs["on_item_result"](1, 1, result_item)
		return BatchScrapeResult(
			run_id=55,
			status="failed",
			total=1,
			successful=0,
			failed=1,
			skipped=0,
			started_at=started_at,
			finished_at=finished_at,
			duration=finished_at - started_at,
			item_results=[result_item],
		)

	monkeypatch.setattr("services.watchlist_service.run_batch", fake_run_batch)
	result = update_watchlist_item(item.id)
	refreshed = db.session.get(WatchlistItem, item.id)

	assert result.run_id == 55
	assert refreshed is not None
	assert refreshed.last_failure_reason == "blocked_by_platform"
	assert WatchlistItem.query.count() == 1
