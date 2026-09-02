"""Transactional scope tests for local data-management operations."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import inspect

from app import create_app
from database.db import db, initialize_database
from database.models import AppSetting, Listing, PriceHistory, Product, ScrapeRun, ScrapeRunItem, Seller, WatchlistItem
from services.data_management_service import (
	DataManagementBlockedError,
	DataManagementConfirmationError,
	DataManagementError,
	clear_marketplace_data,
	clear_price_history,
	clear_scrape_history,
	clear_watchlist,
	reset_all_data,
)
from services.watchlist_service import create_watchlist_item, prepare_active_urls_for_batch, update_active_watchlist_items


URL = "https://www.trendyol.com/casio/f-91w-p-1001"


@pytest.fixture
def app_context(tmp_path: Path):
	app = create_app(
		{
			"TESTING": True,
			"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
			"DISCOVERY_STATE_DIR": tmp_path / "discovery",
		}
	)
	with app.app_context():
		initialize_database(app)
		yield app
		db.session.remove()
		db.drop_all()
		db.engine.dispose()


def seed_all_data(*, running: bool = False) -> None:
	product = Product(name="Casio F-91W", brand="Casio", model="F-91W")
	seller = Seller(platform="trendyol", external_seller_id="seller-1", name="Watch Store")
	listing = Listing(
		product=product,
		seller=seller,
		platform="trendyol",
		external_product_id="1001",
		url=URL,
		current_price=Decimal("799.00"),
		currency="TRY",
	)
	watchlist = WatchlistItem(
		platform="trendyol",
		url=URL,
		external_product_id="1001",
		display_name="Tracked Casio",
		last_scraped_at=datetime.now(timezone.utc),
		last_scrape_status="success",
		last_failure_reason="old_failure",
	)
	run = ScrapeRun(
		platform="trendyol",
		status="running" if running else "completed",
		started_at=datetime.now(timezone.utc),
		products_found=1,
	)
	item = ScrapeRunItem(
		scrape_run=run,
		listing=listing,
		url=URL,
		platform="trendyol",
		status="success",
		started_at=datetime.now(timezone.utc),
	)
	db.session.add_all(
		[
			product,
			seller,
			listing,
			PriceHistory(listing=listing, price=Decimal("799.00")),
			PriceHistory(listing=listing, price=Decimal("749.00")),
			watchlist,
			run,
			item,
			AppSetting(key="discovery_max_pages", value="5"),
		]
	)
	db.session.commit()


def test_clear_price_history_removes_observations_and_preserves_current_data(app_context) -> None:
	seed_all_data()

	result = clear_price_history()

	assert result.deleted_counts == {"price_history": 2}
	assert PriceHistory.query.count() == 0
	assert Listing.query.one().current_price == Decimal("799.00")
	assert Product.query.count() == 1
	assert WatchlistItem.query.count() == 1


def test_clear_scrape_history_removes_runs_only(app_context) -> None:
	seed_all_data()

	result = clear_scrape_history()

	assert result.deleted_counts == {"scrape_run_items": 1, "scrape_runs": 1}
	assert ScrapeRunItem.query.count() == 0
	assert ScrapeRun.query.count() == 0
	assert Product.query.count() == 1
	assert Listing.query.count() == 1
	assert PriceHistory.query.count() == 2
	assert WatchlistItem.query.count() == 1


def test_clear_marketplace_data_preserves_watchlist_and_resets_metadata(app_context) -> None:
	seed_all_data()

	result = clear_marketplace_data()
	tracked = WatchlistItem.query.one()

	assert result.deleted_counts == {
		"price_history": 2,
		"listings": 1,
		"sellers": 1,
		"products": 1,
		"watchlist_metadata_reset": 1,
	}
	assert Product.query.count() == Seller.query.count() == Listing.query.count() == PriceHistory.query.count() == 0
	assert tracked.url == URL
	assert tracked.last_scraped_at is None
	assert tracked.last_scrape_status is None
	assert tracked.last_failure_reason is None
	assert ScrapeRun.query.count() == 1
	assert ScrapeRunItem.query.one().listing_id is None


def test_preserved_watchlist_can_reenter_existing_batch_rebuild_flow(app_context, monkeypatch: pytest.MonkeyPatch) -> None:
	seed_all_data()
	clear_marketplace_data()
	captured: list[list[str]] = []
	sentinel = object()

	def fake_run_batch(urls, **_kwargs):
		captured.append(urls)
		return sentinel

	monkeypatch.setattr("services.watchlist_service.run_batch", fake_run_batch)

	assert prepare_active_urls_for_batch() == [URL]
	assert update_active_watchlist_items() is sentinel
	assert captured == [[URL]]


def test_clear_watchlist_preserves_marketplace_history(app_context) -> None:
	seed_all_data()

	result = clear_watchlist()

	assert result.deleted_counts == {"watchlist_items": 1}
	assert WatchlistItem.query.count() == 0
	assert Product.query.count() == 1
	assert Listing.query.count() == 1
	assert PriceHistory.query.count() == 2


@pytest.mark.parametrize("confirmation", ["", "reset", "RESET ", "DELETE"])
def test_reset_all_rejects_missing_or_incorrect_confirmation_without_changes(app_context, confirmation: str) -> None:
	seed_all_data()

	with pytest.raises(DataManagementConfirmationError):
		reset_all_data(confirmation)

	assert Product.query.count() == 1
	assert PriceHistory.query.count() == 2
	assert WatchlistItem.query.count() == 1
	assert ScrapeRun.query.count() == 1


def test_reset_all_clears_expected_data_but_preserves_schema_and_settings(app_context) -> None:
	seed_all_data()

	result = reset_all_data("RESET")
	table_names = set(inspect(db.engine).get_table_names())

	assert result.deleted_counts == {
		"scrape_run_items": 1,
		"scrape_runs": 1,
		"price_history": 2,
		"listings": 1,
		"sellers": 1,
		"products": 1,
		"watchlist_items": 1,
	}
	assert Product.query.count() == Seller.query.count() == Listing.query.count() == PriceHistory.query.count() == 0
	assert WatchlistItem.query.count() == ScrapeRun.query.count() == ScrapeRunItem.query.count() == 0
	assert db.session.get(AppSetting, "discovery_max_pages").value == "5"
	assert {"products", "listings", "watchlist_items", "scrape_runs", "app_settings"}.issubset(table_names)


def test_watchlist_can_be_added_after_reset_without_recreating_database(app_context) -> None:
	seed_all_data()
	reset_all_data("RESET")

	item = create_watchlist_item(URL, "Fresh Casio")

	assert item.id is not None
	assert WatchlistItem.query.one().display_name == "Fresh Casio"


@pytest.mark.parametrize(
	"operation",
	[clear_price_history, clear_scrape_history, clear_marketplace_data, clear_watchlist],
)
def test_all_clear_operations_are_blocked_while_scrape_run_is_running(app_context, operation) -> None:
	seed_all_data(running=True)

	with pytest.raises(DataManagementBlockedError):
		operation()

	assert Product.query.count() == 1
	assert PriceHistory.query.count() == 2
	assert WatchlistItem.query.count() == 1
	assert ScrapeRun.query.count() == 1


def test_reset_all_is_blocked_while_scrape_run_is_running(app_context) -> None:
	seed_all_data(running=True)

	with pytest.raises(DataManagementBlockedError):
		reset_all_data("RESET")

	assert Product.query.count() == 1


def test_transaction_rolls_back_after_mid_operation_failure(app_context, monkeypatch: pytest.MonkeyPatch) -> None:
	seed_all_data()
	from services import data_management_service

	original_delete = data_management_service._delete_all

	def failing_delete(model):
		if model is Listing:
			raise RuntimeError("simulated delete failure")
		return original_delete(model)

	monkeypatch.setattr(data_management_service, "_delete_all", failing_delete)

	with pytest.raises(DataManagementError):
		clear_marketplace_data()

	assert Product.query.count() == 1
	assert Seller.query.count() == 1
	assert Listing.query.count() == 1
	assert PriceHistory.query.count() == 2
	tracked = WatchlistItem.query.one()
	assert tracked.last_scrape_status == "success"
	assert ScrapeRunItem.query.one().listing_id is not None
