"""Settings page, confirmation, and post-reset route tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from database.db import db, initialize_database
from database.models import AppSetting, Listing, PriceHistory, Product, ScrapeRun, ScrapeRunItem, Seller, WatchlistItem
from discovery import DiscoveryResult


URL = "https://www.trendyol.com/casio/f-91w-p-1001"


@pytest.fixture
def client(tmp_path: Path):
	app = create_app(
		{
			"TESTING": True,
			"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
			"DATA_QUALITY_STALE_HOURS": 48,
			"DISCOVERY_MAX_PAGES": 3,
			"DISCOVERY_MAX_PRODUCTS": 100,
			"DISCOVERY_STATE_DIR": tmp_path / "discovery",
		}
	)
	with app.app_context():
		initialize_database(app)
		with app.test_client() as test_client:
			yield test_client
		db.session.remove()
		db.drop_all()
		db.engine.dispose()


def seed_data(*, running: bool = False) -> None:
	product = Product(name="Casio F-91W", brand="Casio")
	seller = Seller(platform="trendyol", name="Watch Store")
	listing = Listing(
		product=product,
		seller=seller,
		platform="trendyol",
		external_product_id="1001",
		url=URL,
		current_price=Decimal("799.00"),
		currency="TRY",
	)
	run = ScrapeRun(platform="trendyol", status="running" if running else "completed", started_at=datetime.now(timezone.utc))
	db.session.add_all(
		[
			product,
			seller,
			listing,
			PriceHistory(listing=listing, price=Decimal("799.00")),
			WatchlistItem(
				platform="trendyol",
				url=URL,
				external_product_id="1001",
				display_name="Tracked Casio",
				last_scraped_at=datetime.now(timezone.utc),
				last_scrape_status="success",
			),
			run,
			ScrapeRunItem(scrape_run=run, listing=listing, url=URL, platform="trendyol", status="success", started_at=datetime.now(timezone.utc)),
		]
	)
	db.session.commit()


def test_settings_page_loads_and_displays_effective_values(client) -> None:
	response = client.get("/settings")
	body = response.get_data(as_text=True)

	assert response.status_code == 200
	assert "Application Settings" in body
	assert "Data Management" in body
	assert "Danger Zone" in body
	assert 'name="data_quality_stale_hours"' in body and 'value="48"' in body
	assert 'name="discovery_max_pages"' in body and 'value="3"' in body


def test_valid_settings_can_be_updated_from_page(client) -> None:
	response = client.post(
		"/settings/application",
		data={"data_quality_stale_hours": "72", "discovery_max_pages": "5", "discovery_max_products": "150"},
		follow_redirects=True,
	)
	body = response.get_data(as_text=True)

	assert "Application settings saved." in body
	assert db.session.get(AppSetting, "data_quality_stale_hours").value == "72"
	assert 'name="discovery_max_products"' in body and 'value="150"' in body


def test_invalid_settings_are_rejected_and_unsafe_input_is_escaped(client) -> None:
	response = client.post(
		"/settings/application",
		data={
			"data_quality_stale_hours": "<script>alert(1)</script>",
			"discovery_max_pages": "0",
			"discovery_max_products": "201",
		},
	)
	body = response.get_data(as_text=True)

	assert response.status_code == 200
	assert "Enter a whole number." in body
	assert "Enter a value from 1 to 10." in body
	assert "<script>alert(1)</script>" not in body
	assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
	assert AppSetting.query.count() == 0


def test_restore_defaults_does_not_delete_data(client) -> None:
	seed_data()
	client.post(
		"/settings/application",
		data={"data_quality_stale_hours": "72", "discovery_max_pages": "5", "discovery_max_products": "150"},
	)

	response = client.post("/settings/application/defaults", follow_redirects=True)

	assert "Default application settings restored" in response.get_data(as_text=True)
	assert AppSetting.query.count() == 0
	assert Product.query.count() == 1
	assert WatchlistItem.query.count() == 1


def test_persisted_discovery_limits_are_used_by_discovery_route(client, monkeypatch: pytest.MonkeyPatch) -> None:
	client.post(
		"/settings/application",
		data={"data_quality_stale_hours": "72", "discovery_max_pages": "6", "discovery_max_products": "140"},
	)
	captured: list[dict[str, object]] = []

	def fake_discover(platform, brand, **kwargs):
		captured.append({"platform": platform, "brand": brand, **kwargs})
		return DiscoveryResult("trendyol", "Casio", "success", "https://www.trendyol.com/sr?q=Casio")

	monkeypatch.setattr("app.routes.discover_products", fake_discover)
	response = client.post("/discovery/preview", data={"platform": "trendyol", "brand": "Casio"})

	assert response.status_code == 200
	assert captured[0]["max_pages"] == 6
	assert captured[0]["max_products"] == 140


def test_clear_price_history_route_reports_real_count_and_preserves_listing(client) -> None:
	seed_data()

	response = client.post(
		"/settings/data/price-history",
		data={"confirmation": "CLEAR_PRICE_HISTORY"},
		follow_redirects=True,
	)

	assert "Price history cleared: 1 price record(s) removed" in response.get_data(as_text=True)
	assert PriceHistory.query.count() == 0
	assert Listing.query.count() == 1
	assert WatchlistItem.query.count() == 1


def test_clear_scrape_history_route_preserves_marketplace_data(client) -> None:
	seed_data()

	response = client.post(
		"/settings/data/scrape-history",
		data={"confirmation": "CLEAR_SCRAPE_HISTORY"},
		follow_redirects=True,
	)

	assert "Scrape history cleared: 1 run(s) and 1 item result(s) removed" in response.get_data(as_text=True)
	assert ScrapeRun.query.count() == ScrapeRunItem.query.count() == 0
	assert Product.query.count() == PriceHistory.query.count() == 1


def test_clear_marketplace_route_requires_strong_confirmation_and_preserves_watchlist(client) -> None:
	seed_data()

	rejected = client.post(
		"/settings/data/marketplace",
		data={"confirmation": "wrong"},
		follow_redirects=True,
	)
	accepted = client.post(
		"/settings/data/marketplace",
		data={"confirmation": "CLEAR MARKETPLACE"},
		follow_redirects=True,
	)

	assert "No data was deleted" in rejected.get_data(as_text=True)
	assert "Marketplace data cleared" in accepted.get_data(as_text=True)
	assert Product.query.count() == Listing.query.count() == PriceHistory.query.count() == 0
	assert WatchlistItem.query.count() == 1
	assert WatchlistItem.query.one().last_scrape_status is None


def test_clear_watchlist_route_preserves_marketplace_history(client) -> None:
	seed_data()

	response = client.post(
		"/settings/data/watchlist",
		data={"confirmation": "CLEAR WATCHLIST"},
		follow_redirects=True,
	)

	assert "Watchlist cleared: 1 tracked URL(s) removed" in response.get_data(as_text=True)
	assert WatchlistItem.query.count() == 0
	assert Product.query.count() == Listing.query.count() == PriceHistory.query.count() == 1


@pytest.mark.parametrize("confirmation", [None, "", "reset", "RESET "])
def test_reset_route_rejects_missing_or_incorrect_confirmation(client, confirmation: str | None) -> None:
	seed_data()
	payload = {} if confirmation is None else {"confirmation": confirmation}

	response = client.post("/settings/data/reset", data=payload, follow_redirects=True)

	assert "Type RESET exactly" in response.get_data(as_text=True)
	assert Product.query.count() == 1
	assert WatchlistItem.query.count() == 1
	assert ScrapeRun.query.count() == 1


def test_exact_reset_clears_data_and_preserves_settings(client) -> None:
	seed_data()
	db.session.add(AppSetting(key="discovery_max_pages", value="5"))
	db.session.commit()

	response = client.post("/settings/data/reset", data={"confirmation": "RESET"}, follow_redirects=True)

	assert "All local business and operational data reset" in response.get_data(as_text=True)
	assert Product.query.count() == Seller.query.count() == Listing.query.count() == PriceHistory.query.count() == 0
	assert WatchlistItem.query.count() == ScrapeRun.query.count() == ScrapeRunItem.query.count() == 0
	assert db.session.get(AppSetting, "discovery_max_pages").value == "5"


@pytest.mark.parametrize(
	"path",
	[
		"/settings/data/price-history",
		"/settings/data/scrape-history",
		"/settings/data/marketplace",
		"/settings/data/watchlist",
		"/settings/data/reset",
	],
)
def test_destructive_get_requests_are_not_supported(client, path: str) -> None:
	assert client.get(path).status_code == 405


def test_destructive_route_is_blocked_during_running_scrape(client) -> None:
	seed_data(running=True)

	response = client.post(
		"/settings/data/price-history",
		data={"confirmation": "CLEAR_PRICE_HISTORY"},
		follow_redirects=True,
	)

	assert "cannot be cleared while scraping run" in response.get_data(as_text=True)
	assert PriceHistory.query.count() == 1


def test_major_pages_render_empty_states_after_full_reset(client) -> None:
	seed_data()
	client.post("/settings/data/reset", data={"confirmation": "RESET"})

	for path in (
		"/",
		"/products",
		"/sellers",
		"/watchlist",
		"/data-quality",
		"/scraping",
		"/discovery",
		"/scrape-runs",
		"/import",
		"/settings",
	):
		response = client.get(path)
		assert response.status_code == 200, path


def test_watchlist_can_be_populated_from_page_after_reset(client) -> None:
	seed_data()
	client.post("/settings/data/reset", data={"confirmation": "RESET"})

	response = client.post("/watchlist", data={"url": URL, "label": "Fresh Casio"}, follow_redirects=True)

	assert "Product added to the watchlist." in response.get_data(as_text=True)
	assert WatchlistItem.query.count() == 1
