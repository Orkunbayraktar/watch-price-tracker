"""Route tests for the Brand Discovery review workflow."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from bs4 import BeautifulSoup
import pytest

from app import create_app
from database.db import db, initialize_database
from database.models import PriceHistory, WatchlistItem
from discovery import DiscoveredProduct, DiscoveryResult


URL_1 = "https://www.trendyol.com/casio/f-91w-p-1001"
URL_2 = "https://www.trendyol.com/casio/ga-2100-p-1002"


@pytest.fixture
def client(tmp_path: Path):
	app = create_app(
		{
			"TESTING": True,
			"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
			"DISCOVERY_STATE_DIR": tmp_path / "discovery-state",
			"DISCOVERY_PREVIEW_TTL_SECONDS": 60,
		}
	)
	with app.app_context():
		initialize_database(app)
		with app.test_client() as test_client:
			yield test_client
		db.session.remove()
		db.drop_all()
		db.engine.dispose()


def successful_result() -> DiscoveryResult:
	return DiscoveryResult(
		platform="trendyol",
		brand="Casio",
		status="success",
		source_url="https://www.trendyol.com/sr?q=Casio&pi=1",
		products=(
			DiscoveredProduct("trendyol", URL_1, "1001", "Casio F-91W", "Casio", Decimal("799.90"), "TRY"),
			DiscoveredProduct("trendyol", URL_2, "1002", "Casio GA-2100", "Casio"),
		),
		pages_scanned=1,
		message="Discovery completed.",
	)


def preview(client, monkeypatch: pytest.MonkeyPatch, result: DiscoveryResult | None = None):
	monkeypatch.setattr("app.routes.discover_products", lambda *_args, **_kwargs: result or successful_result())
	return client.post("/discovery/preview", data={"platform": "trendyol", "brand": "Casio", "max_products": "20"})


def preview_token(response) -> str:
	soup = BeautifulSoup(response.get_data(as_text=True), "html.parser")
	return soup.select_one("input[name='preview_token']")["value"]


def test_discovery_page_loads(client) -> None:
	response = client.get("/discovery")

	assert response.status_code == 200
	assert b"Brand Discovery" in response.data
	assert b"Discover Products" in response.data


def test_preview_renders_products_and_does_not_put_payload_in_cookie(client, monkeypatch: pytest.MonkeyPatch) -> None:
	response = preview(client, monkeypatch)
	body = response.get_data(as_text=True)

	assert response.status_code == 200
	assert "Casio F-91W" in body
	assert "799,90 TL" in body
	assert "Add Selected to Watchlist" in body
	assert all("Casio F-91W" not in header for header in response.headers.getlist("Set-Cookie"))
	assert PriceHistory.query.count() == 0


def test_already_tracked_product_is_marked_and_disabled(client, monkeypatch: pytest.MonkeyPatch) -> None:
	db.session.add(WatchlistItem(platform="trendyol", url=URL_1, external_product_id="1001"))
	db.session.commit()

	response = preview(client, monkeypatch)
	soup = BeautifulSoup(response.get_data(as_text=True), "html.parser")

	assert "Already tracked" in response.get_data(as_text=True)
	assert soup.select_one("input[value='https://www.trendyol.com/casio/f-91w-p-1001']").has_attr("disabled")


def test_selected_products_are_added_without_duplicates(client, monkeypatch: pytest.MonkeyPatch) -> None:
	response = preview(client, monkeypatch)
	token = preview_token(response)

	added = client.post("/discovery/add", data={"preview_token": token, "selected_urls": [URL_1, URL_2]})
	repeated = client.post("/discovery/add", data={"preview_token": token, "selected_urls": [URL_1]})

	assert added.status_code == 200
	assert "2 selected product(s) added" in added.get_data(as_text=True)
	assert "Update Added Products" in added.get_data(as_text=True)
	assert "No new products were added" in repeated.get_data(as_text=True)
	assert WatchlistItem.query.count() == 2
	assert PriceHistory.query.count() == 0


def test_no_selection_is_handled_safely(client, monkeypatch: pytest.MonkeyPatch) -> None:
	token = preview_token(preview(client, monkeypatch))

	response = client.post("/discovery/add", data={"preview_token": token})

	assert "Select at least one new product" in response.get_data(as_text=True)
	assert WatchlistItem.query.count() == 0


def test_tampered_product_url_is_not_added(client, monkeypatch: pytest.MonkeyPatch) -> None:
	token = preview_token(preview(client, monkeypatch))

	response = client.post("/discovery/add", data={"preview_token": token, "selected_urls": "https://www.trendyol.com/other/not-in-preview-p-9999"})

	assert response.status_code == 200
	assert WatchlistItem.query.count() == 0


@pytest.mark.parametrize(
	("reason", "expected"),
	[
		("invalid_brand", "Invalid brand"),
		("robots_denied", "robots.txt denied discovery"),
		("robots_load_failed", "robots.txt unavailable"),
		("blocked_by_platform", "Blocked by platform"),
		("discovery_parse_failed", "Discovery parse failed"),
		("timeout", "Discovery timed out"),
	],
)
def test_structured_error_states_render(client, monkeypatch: pytest.MonkeyPatch, reason: str, expected: str) -> None:
	result = DiscoveryResult("hepsiburada", "Casio", "failed", None, failure_reason=reason, message="Safe failure message.")

	response = preview(client, monkeypatch, result)

	assert expected in response.get_data(as_text=True)
	assert "Safe failure message." in response.get_data(as_text=True)


@pytest.fixture
def offline_catalog_provider(monkeypatch):
	import requests
	from services.product_discovery_service import DEFAULT_PROVIDER_FACTORIES
	from tests.test_saatvesaat_discovery import make_provider

	def no_network(*args, **kwargs):
		pytest.fail("Offline route test attempted an HTTP request")

	monkeypatch.setattr(requests.sessions.Session, "request", no_network)
	provider, fetcher, robots = make_provider()
	monkeypatch.setitem(DEFAULT_PROVIDER_FACTORIES, "saatvesaat", lambda: provider)
	return provider, fetcher, robots


def test_catalog_form_defaults_and_safe_choices(client):
	soup = BeautifulSoup(client.get("/discovery").data, "html.parser")
	assert soup.select_one("select[name=platform] option[selected]")["value"] == "saatvesaat"
	assert soup.select_one("select[name=max_products] option[selected]")["value"] == "100"
	assert soup.select_one("input[name=max_catalogs]")["value"] == "15"
	assert soup.select_one("input[name=max_catalogs]")["max"] == "25"
	assert len(soup.select("input[name=catalog_ids]:checked")) == 25
	assert soup.select_one("option[value=hepsiburada]").has_attr("disabled")
	assert soup.select_one("input[name=brand]").has_attr("disabled")
	assert not soup.select_one("fieldset[data-catalog-control]").has_attr("disabled")


def test_default_catalog_target_does_not_inherit_trendyol_setting(client, offline_catalog_provider):
	client.application.config["DISCOVERY_MAX_PRODUCTS"] = 140
	response = client.post("/discovery/preview", data={"platform": " SaatVeSaat ", "catalog_ids": ["fossil"]})
	soup = BeautifulSoup(response.data, "html.parser")
	assert soup.select_one("select[name=max_products] option[selected]")["value"] == "100"
	assert len(soup.select("[data-discovery-row]")) == 12
	assert "Partial results are ready to review" in response.get_data(as_text=True)


def test_large_combined_preview_bulk_import_and_legacy_tracked_identity(client, offline_catalog_provider):
	from discovery.saatvesaat import CATALOG_TARGETS
	from database.models import Listing, Product
	from services.product_identity_service import product_identity

	# A pre-existing paused URL with host, slug, case, and tracking differences.
	db.session.add(WatchlistItem(platform="saatvesaat", url="http://saatvesaat.com.tr/old-slug-p-sku0-0/?utm_source=old",
							 external_product_id="SKU0-0", is_active=False))
	db.session.commit()
	response = client.post("/discovery/preview", data={
		"platform": "saatvesaat", "max_products": "200", "max_catalogs": "25",
		"catalog_ids": [catalog.id for catalog in CATALOG_TARGETS],
	})
	assert response.status_code == 200
	soup = BeautifulSoup(response.data, "html.parser")
	items = soup.select("[data-discovery-item]")
	assert len(items) == 200
	assert len(soup.select("[data-discovery-item][disabled]")) == 1
	assert items[-1].has_attr("disabled")  # New products are shown first.
	for label in ("Catalogs scanned", "Products discovered", "New products", "Already tracked", "Duplicates skipped", "Catalog failures/skips"):
		assert label in response.get_data(as_text=True)
	token = preview_token(response)
	selected = [item["value"] for item in items]  # Tampered selection includes the tracked row.
	added = client.post("/discovery/add", data={"preview_token": token, "selected_urls": selected})
	assert "199 selected product(s) added" in added.get_data(as_text=True)
	assert WatchlistItem.query.count() == 200
	assert len({product_identity(row.url) for row in WatchlistItem.query.all()}) == 200
	assert PriceHistory.query.count() == Product.query.count() == Listing.query.count() == 0
	assert "Update Added Products" in added.get_data(as_text=True)
	assert len(offline_catalog_provider[1].calls) == 17
	# Reusing the saved preview neither fetches again nor duplicates Watchlist rows.
	again = client.post("/discovery/add", data={"preview_token": token, "selected_urls": selected})
	assert "No new products were added" in again.get_data(as_text=True)
	assert WatchlistItem.query.count() == 200
	assert len(offline_catalog_provider[1].calls) == 17


def test_partial_failure_preview_can_be_imported_and_preserves_catalog_selection(client, offline_catalog_provider):
	from scrapers.fetchers import FetcherError
	fetcher = offline_catalog_provider[1]
	fetcher.overrides["https://www.saatvesaat.com.tr/seiko"] = FetcherError("navigation_error", "Offline failure")
	response = client.post("/discovery/preview", data={
		"platform": "saatvesaat", "max_products": "100", "max_catalogs": "15",
		"catalog_ids": ["fossil", "seiko", "timex"],
	})
	body = response.get_data(as_text=True)
	assert "Partial results are ready to review" in body
	assert "navigation error" in body
	soup = BeautifulSoup(body, "html.parser")
	selected = [item["value"] for item in soup.select("[data-discovery-item]")]
	assert len(selected) == 24
	added = client.post("/discovery/add", data={"preview_token": preview_token(response), "selected_urls": selected})
	assert WatchlistItem.query.count() == 24
	after = BeautifulSoup(added.data, "html.parser")
	assert {item["value"] for item in after.select("input[name=catalog_ids]:checked")} == {"fossil", "seiko", "timex"}
	assert after.select_one("select[name=max_products] option[selected]")["value"] == "100"
	assert "navigation error" in added.get_data(as_text=True)


@pytest.mark.parametrize("values", [
	{"max_products": "201"}, {"max_products": "24"}, {"max_products": "100.5"},
	{"max_products": "bad"}, {"max_catalogs": "26"}, {"catalog_ids": []},
	{"catalog_ids": ["https://evil.example"]}, {"catalog_ids": ["fossil?p=2"]},
])
def test_invalid_catalog_form_does_not_fetch_or_create_preview(client, offline_catalog_provider, values):
	data = {"platform": "saatvesaat", "max_products": "100", "max_catalogs": "15", "catalog_ids": ["fossil"]}
	data.update(values)
	response = client.post("/discovery/preview", data=data)
	assert response.status_code == 200
	assert not BeautifulSoup(response.data, "html.parser").select("input[name=preview_token]")
	assert offline_catalog_provider[1].calls == []
	assert WatchlistItem.query.count() == 0
