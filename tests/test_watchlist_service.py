"""Tests for watchlist service behavior and batch update integration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import create_app
from database.db import db, initialize_database
from database.models import WatchlistItem
from services.batch_scraping_service import BatchScrapeItemResult, BatchScrapeResult
from services.watchlist_service import (
	DEFAULT_WATCHLIST_FETCH_STRATEGY,
	prepare_active_urls_for_batch,
	update_active_watchlist_items,
)


TRENDYOL_URL = "https://www.trendyol.com/casio/f-91w-p-1001"
HEPSIBURADA_URL = "https://www.hepsiburada.com/casio-retro-kol-saati-a159wa-n1df-pm-sacsa159wan1df"


@pytest.fixture
def app_context(tmp_path: Path):
	app = create_app(
		{
			"TESTING": True,
			"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
			"IMPORT_STATE_DIR": tmp_path / "import-state",
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


def add_watchlist_item(url: str, *, platform: str, external_product_id: str, is_active: bool = True) -> WatchlistItem:
	item = WatchlistItem(
		platform=platform,
		url=url,
		external_product_id=external_product_id,
		is_active=is_active,
	)
	db.session.add(item)
	db.session.commit()
	return item


def as_sqlite_utc(value: datetime) -> datetime:
	return value.replace(tzinfo=None)


def test_prepare_active_urls_excludes_paused_items(app_context) -> None:
	add_watchlist_item(TRENDYOL_URL, platform="trendyol", external_product_id="1001", is_active=True)
	add_watchlist_item(HEPSIBURADA_URL, platform="hepsiburada", external_product_id="SACSA159WAN1DF", is_active=False)

	assert prepare_active_urls_for_batch() == [TRENDYOL_URL]


def test_watchlist_update_reuses_batch_service_and_updates_success_metadata(app_context, monkeypatch: pytest.MonkeyPatch) -> None:
	active_item = add_watchlist_item(TRENDYOL_URL, platform="trendyol", external_product_id="1001", is_active=True)
	paused_item = add_watchlist_item(HEPSIBURADA_URL, platform="hepsiburada", external_product_id="SACSA159WAN1DF", is_active=False)
	started_at = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
	finished_at = datetime(2026, 9, 1, 9, 1, tzinfo=timezone.utc)

	def fake_run_batch(urls, platform=None, fetch_strategy="requests", **kwargs):
		assert urls == [TRENDYOL_URL]
		assert platform is None
		assert fetch_strategy == DEFAULT_WATCHLIST_FETCH_STRATEGY
		item_result = BatchScrapeItemResult(
			url=TRENDYOL_URL,
			platform="trendyol",
			status="success",
			failure_reason=None,
			error_message=None,
			listing_id=11,
			product_name="Casio F-91W",
			current_price=None,
			currency="TRY",
			started_at=started_at,
			finished_at=finished_at,
		)
		kwargs["on_item_result"](1, 1, item_result)
		return BatchScrapeResult(
			run_id=12,
			status="completed",
			total=1,
			successful=1,
			failed=0,
			skipped=0,
			started_at=started_at,
			finished_at=finished_at,
			duration=finished_at - started_at,
			item_results=[item_result],
		)

	monkeypatch.setattr("services.watchlist_service.run_batch", fake_run_batch)
	result = update_active_watchlist_items()

	refreshed_active = db.session.get(WatchlistItem, active_item.id)
	refreshed_paused = db.session.get(WatchlistItem, paused_item.id)

	assert result is not None
	assert result.run_id == 12
	assert refreshed_active.last_scrape_status == "success"
	assert refreshed_active.last_failure_reason is None
	assert refreshed_active.last_scraped_at == as_sqlite_utc(finished_at)
	assert refreshed_paused.last_scrape_status is None


def test_failed_watchlist_update_stores_structured_failure_reason_and_keeps_item(app_context, monkeypatch: pytest.MonkeyPatch) -> None:
	item = add_watchlist_item(HEPSIBURADA_URL, platform="hepsiburada", external_product_id="SACSA159WAN1DF", is_active=True)
	started_at = datetime(2026, 9, 1, 11, 0, tzinfo=timezone.utc)
	finished_at = started_at + timedelta(minutes=2)

	def fake_run_batch(urls, fetch_strategy="requests", **kwargs):
		assert urls == [HEPSIBURADA_URL]
		assert fetch_strategy == DEFAULT_WATCHLIST_FETCH_STRATEGY
		item_result = BatchScrapeItemResult(
			url=HEPSIBURADA_URL,
			platform="hepsiburada",
			status="failed",
			failure_reason="blocked_by_platform",
			error_message="The platform blocked browser navigation with HTTP 403.",
			listing_id=None,
			product_name=None,
			current_price=None,
			currency=None,
			started_at=started_at,
			finished_at=finished_at,
		)
		kwargs["on_item_result"](1, 1, item_result)
		return BatchScrapeResult(
			run_id=99,
			status="failed",
			total=1,
			successful=0,
			failed=1,
			skipped=0,
			started_at=started_at,
			finished_at=finished_at,
			duration=finished_at - started_at,
			item_results=[item_result],
		)

	monkeypatch.setattr("services.watchlist_service.run_batch", fake_run_batch)
	result = update_active_watchlist_items()
	refreshed_item = db.session.get(WatchlistItem, item.id)

	assert result is not None
	assert refreshed_item is not None
	assert refreshed_item.last_scrape_status == "failed"
	assert refreshed_item.last_failure_reason == "blocked_by_platform"
	assert refreshed_item.last_scraped_at == as_sqlite_utc(finished_at)
	assert WatchlistItem.query.count() == 1
