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
