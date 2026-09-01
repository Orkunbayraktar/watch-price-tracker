"""Service tests for Scraping Control Center composition and orchestration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from database.db import db, initialize_database
from database.models import Listing, Product, ScrapeRun, ScrapeRunItem, WatchlistItem
from services.batch_scraping_service import BatchScrapeItemResult, BatchScrapeResult
from services.scraping_control_service import (
	ScrapingControlValidationError,
	ScrapingRunInProgressError,
	get_scraping_control_data,
	scrape_one_product,
	update_all_active_products,
	update_selected_products,
)
from services.watchlist_service import WatchlistValidationError


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
TRENDYOL_URL = "https://www.trendyol.com/casio/f-91w-p-1001"
HEPSIBURADA_URL = "https://www.hepsiburada.com/casio-retro-pm-hbcv00001"


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


def add_watchlist_item(
	url: str,
	*,
	platform: str,
	name: str,
	is_active: bool = True,
) -> WatchlistItem:
	item = WatchlistItem(
		platform=platform,
		url=url,
		external_product_id=url.rsplit("-", 1)[-1],
		display_name=name,
		is_active=is_active,
	)
	db.session.add(item)
	db.session.commit()
	return item


def make_batch_result(
	url: str,
	*,
	status: str = "success",
	failure_reason: str | None = None,
	run_id: int = 20,
) -> BatchScrapeResult:
	item_result = BatchScrapeItemResult(
		url=url,
		platform="hepsiburada" if "hepsiburada" in url else "trendyol",
		status=status,
		failure_reason=failure_reason,
		error_message=None,
		listing_id=8 if status == "success" else None,
		product_name="Casio F-91W" if status == "success" else None,
		current_price=Decimal("799.00") if status == "success" else None,
		currency="TRY" if status == "success" else None,
		started_at=NOW,
		finished_at=NOW + timedelta(minutes=1),
	)
	return BatchScrapeResult(
		run_id=run_id,
		status="completed" if status == "success" else "failed",
		total=1,
		successful=1 if status == "success" else 0,
		failed=0 if status == "success" else 1,
		skipped=0,
		started_at=NOW,
		finished_at=NOW + timedelta(minutes=1),
		duration=timedelta(minutes=1),
		item_results=[item_result],
	)


def test_control_data_uses_real_watchlist_listing_and_run_values(app_context) -> None:
	active = add_watchlist_item(TRENDYOL_URL, platform="trendyol", name="Active Casio")
	add_watchlist_item(HEPSIBURADA_URL, platform="hepsiburada", name="Paused Casio", is_active=False)
	product = Product(name="Casio F-91W")
	listing = Listing(
		product=product,
		platform="trendyol",
		external_product_id=active.external_product_id,
		url=active.url,
		current_price=Decimal("799.00"),
		currency="TRY",
	)
	run = ScrapeRun(
		platform="trendyol",
		started_at=NOW,
		finished_at=NOW + timedelta(minutes=1),
		status="completed",
		products_found=1,
		listings_found=1,
		errors_count=0,
	)
	db.session.add_all([product, listing, run])
	db.session.commit()

	control = get_scraping_control_data()

	assert control.active_count == 1
	assert control.paused_count == 1
	assert control.latest_run is not None
	assert control.latest_run.run_id == run.id
	assert control.watchlist_items[0].linked_listing.current_price == Decimal("799.00")
	assert control.watchlist_items[0].health.key == "never_scraped"


def test_selected_update_passes_only_active_ids_and_reports_paused(app_context, monkeypatch: pytest.MonkeyPatch) -> None:
	active = add_watchlist_item(TRENDYOL_URL, platform="trendyol", name="Active")
	paused = add_watchlist_item(HEPSIBURADA_URL, platform="hepsiburada", name="Paused", is_active=False)
	captured: list[list[int]] = []

	monkeypatch.setattr("services.scraping_control_service.get_running_scrape_run_summary", lambda: None)
	monkeypatch.setattr(
		"services.scraping_control_service.update_selected_watchlist_items",
		lambda item_ids: captured.append(item_ids) or make_batch_result(active.url),
	)

	result = update_selected_products([str(active.id), str(paused.id)])

	assert captured == [[active.id]]
	assert result.skipped_paused == 1
	assert result.batch_result is not None


def test_empty_selected_update_is_rejected_safely(app_context) -> None:
	with pytest.raises(ScrapingControlValidationError, match="Select at least one"):
		update_selected_products([])


def test_paused_only_selection_does_not_start_batch(app_context, monkeypatch: pytest.MonkeyPatch) -> None:
	paused = add_watchlist_item(HEPSIBURADA_URL, platform="hepsiburada", name="Paused", is_active=False)
	called = False

	def fake_update(_item_ids):
		nonlocal called
		called = True

	monkeypatch.setattr("services.scraping_control_service.update_selected_watchlist_items", fake_update)
	result = update_selected_products([paused.id])

	assert result.batch_result is None
	assert result.skipped_paused == 1
	assert called is False


def test_update_all_reuses_watchlist_update_service(app_context, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr("services.scraping_control_service.get_running_scrape_run_summary", lambda: None)
	monkeypatch.setattr(
		"services.scraping_control_service.update_active_watchlist_items",
		lambda: make_batch_result(TRENDYOL_URL),
	)

	result = update_all_active_products()

	assert result.batch_result is not None
	assert result.batch_result.run_id == 20


def test_manual_trendyol_url_uses_existing_batch_service_with_playwright(app_context, monkeypatch: pytest.MonkeyPatch) -> None:
	captured: dict[str, object] = {}
	monkeypatch.setattr("services.scraping_control_service.get_running_scrape_run_summary", lambda: None)

	def fake_run_batch(urls, fetch_strategy="requests", **_kwargs):
		captured["urls"] = urls
		captured["fetch_strategy"] = fetch_strategy
		return make_batch_result(urls[0])

	monkeypatch.setattr("services.scraping_control_service.run_batch", fake_run_batch)
	result = scrape_one_product(f"  {TRENDYOL_URL}#reviews  ")

	assert captured == {"urls": [TRENDYOL_URL], "fetch_strategy": "playwright"}
	assert result.single_item.product_name == "Casio F-91W"


@pytest.mark.parametrize("url", ["https://example.com/watch", "javascript:alert(1)", "file:///tmp/watch", "data:text/plain,test", "ftp://example.com/watch"])
def test_manual_unsupported_and_unsafe_urls_are_rejected(app_context, url: str) -> None:
	with pytest.raises(WatchlistValidationError) as error:
		scrape_one_product(url)

	assert "URL" in str(error.value) or "HTTP" in str(error.value) or "supported" in str(error.value)


def test_hepsiburada_blocked_result_uses_data_quality_failure_mapping(app_context, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr("services.scraping_control_service.get_running_scrape_run_summary", lambda: None)
	monkeypatch.setattr(
		"services.scraping_control_service.run_batch",
		lambda *_args, **_kwargs: make_batch_result(
			HEPSIBURADA_URL,
			status="failed",
			failure_reason="blocked_by_platform",
		),
	)

	result = scrape_one_product(HEPSIBURADA_URL)

	assert result.failed_items[0].failure_reason == "blocked_by_platform"
	assert result.failed_items[0].failure_message == "Platform blocked browser request"


def test_running_run_blocks_new_control_operation(app_context, monkeypatch: pytest.MonkeyPatch) -> None:
	run = ScrapeRun(platform="trendyol", status="running", started_at=NOW)
	db.session.add(run)
	db.session.commit()
	called = False

	def fake_update():
		nonlocal called
		called = True

	monkeypatch.setattr("services.scraping_control_service.update_active_watchlist_items", fake_update)
	with pytest.raises(ScrapingRunInProgressError, match="already in progress"):
		update_all_active_products()

	assert called is False


def test_recent_failures_use_structured_reason_and_watchlist_label(app_context) -> None:
	item = add_watchlist_item(HEPSIBURADA_URL, platform="hepsiburada", name="Blocked Casio")
	run = ScrapeRun(
		platform="hepsiburada",
		status="failed",
		started_at=NOW,
		finished_at=NOW + timedelta(minutes=1),
		errors_count=1,
	)
	attempt = ScrapeRunItem(
		scrape_run=run,
		url=item.url,
		platform=item.platform,
		status="failed",
		failure_reason="blocked_by_platform",
		started_at=NOW,
		finished_at=NOW + timedelta(minutes=1),
	)
	db.session.add_all([run, attempt])
	db.session.commit()

	control = get_scraping_control_data()

	assert control.recent_failures[0].product_label == "Blocked Casio"
	assert control.recent_failures[0].failure_reason == "blocked_by_platform"
	assert control.recent_failures[0].failure_message == "Platform blocked browser request"
