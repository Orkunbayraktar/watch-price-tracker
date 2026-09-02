"""Tests for watchlist management routes and HTML rendering."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from database.db import db, initialize_database
from database.models import Listing, PriceHistory, Product, WatchlistItem
from services.batch_scraping_service import BatchScrapeResult


TRENDYOL_URL = "https://www.trendyol.com/casio/f-91w-p-1001"
HEPSIBURADA_URL = "https://www.hepsiburada.com/casio-retro-kol-saati-a159wa-n1df-pm-sacsa159wan1df?magaza=watch-store"


@pytest.fixture
def client(tmp_path: Path):
	app = create_app(
		{
			"TESTING": True,
			"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
			"IMPORT_STATE_DIR": tmp_path / "import-state",
		}
	)
	with app.app_context():
		initialize_database(app)
		with app.test_client() as test_client:
			yield test_client
		db.session.remove()
		db.drop_all()
		db.engine.dispose()


def create_watchlist_item(**overrides: object) -> WatchlistItem:
	payload = {
		"platform": "trendyol",
		"url": TRENDYOL_URL,
		"external_product_id": "1001",
		"display_name": "Casio Track",
		"is_active": True,
	}
	payload.update(overrides)
	item = WatchlistItem(**payload)
	db.session.add(item)
	db.session.commit()
	return item


def test_watchlist_page_loads(client) -> None:
	response = client.get("/watchlist")

	assert response.status_code == 200
	assert b"Watchlist" in response.data


def test_watchlist_empty_state_renders(client) -> None:
	response = client.get("/watchlist")
	body = response.get_data(as_text=True)

	assert "No products are being tracked yet." in body
	assert "Add a Trendyol, Hepsiburada, or Saat&amp;Saat product URL to start monitoring prices." in body


def test_valid_trendyol_url_can_be_added(client) -> None:
	response = client.post(
		"/watchlist",
		data={"url": f"  HTTPS://WWW.TRENDYOL.COM/casio/f-91w-p-1001#reviews  ", "label": "Daily Trendyol"},
		follow_redirects=True,
	)

	body = response.get_data(as_text=True)
	item = WatchlistItem.query.one()

	assert response.status_code == 200
	assert "Product added to the watchlist." in body
	assert item.platform == "trendyol"
	assert item.url == TRENDYOL_URL
	assert item.external_product_id == "1001"
	assert item.display_name == "Daily Trendyol"


def test_valid_hepsiburada_url_can_be_added(client) -> None:
	client.post("/watchlist", data={"url": HEPSIBURADA_URL}, follow_redirects=True)

	item = WatchlistItem.query.one()

	assert item.platform == "hepsiburada"
	assert item.url == HEPSIBURADA_URL
	assert item.external_product_id == "SACSA159WAN1DF"


def test_unsupported_domain_is_rejected(client) -> None:
	response = client.post("/watchlist", data={"url": "https://example.com/not-supported"})
	body = response.get_data(as_text=True)

	assert response.status_code == 200
	assert "Only supported Trendyol, Hepsiburada, and Saat&amp;Saat product URLs can be tracked." in body
	assert WatchlistItem.query.count() == 0


def test_malformed_url_is_rejected(client) -> None:
	response = client.post("/watchlist", data={"url": "not a url"})
	body = response.get_data(as_text=True)

	assert response.status_code == 200
	assert "Enter a valid HTTP or HTTPS marketplace product URL." in body
	assert WatchlistItem.query.count() == 0


def test_duplicate_url_is_rejected_after_canonicalization(client) -> None:
	client.post("/watchlist", data={"url": "https://www.trendyol.com/casio/f-91w-p-1001#comments"}, follow_redirects=True)
	response = client.post("/watchlist", data={"url": TRENDYOL_URL}, follow_redirects=True)
	body = response.get_data(as_text=True)

	assert "This product URL is already being tracked." in body
	assert WatchlistItem.query.count() == 1


def test_watchlist_item_can_be_paused(client) -> None:
	item = create_watchlist_item()

	response = client.post(f"/watchlist/{item.id}/toggle", follow_redirects=True)
	body = response.get_data(as_text=True)

	assert "Watchlist item paused." in body
	assert db.session.get(WatchlistItem, item.id).is_active is False


def test_paused_watchlist_item_can_be_activated(client) -> None:
	item = create_watchlist_item(is_active=False)

	response = client.post(f"/watchlist/{item.id}/toggle", follow_redirects=True)
	body = response.get_data(as_text=True)

	assert "Watchlist item activated." in body
	assert db.session.get(WatchlistItem, item.id).is_active is True


def test_watchlist_item_can_be_removed(client) -> None:
	item = create_watchlist_item()

	response = client.post(f"/watchlist/{item.id}/delete", follow_redirects=True)
	body = response.get_data(as_text=True)

	assert "Watchlist item removed." in body
	assert WatchlistItem.query.count() == 0


def test_removing_watchlist_item_does_not_delete_product_listing_or_history(client) -> None:
	product = Product(name="Casio F-91W", brand="Casio", model="F-91W")
	listing = Listing(
		product=product,
		platform="trendyol",
		external_product_id="1001",
		url=TRENDYOL_URL,
		current_price=Decimal("799.00"),
		currency="TRY",
		last_scraped_at=datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
	)
	db.session.add_all([product, listing])
	db.session.flush()
	db.session.add(PriceHistory(listing=listing, price=Decimal("799.00")))
	db.session.commit()
	item = create_watchlist_item()

	client.post(f"/watchlist/{item.id}/delete", follow_redirects=True)

	assert Product.query.count() == 1
	assert Listing.query.count() == 1
	assert PriceHistory.query.count() == 1


def test_watchlist_page_escapes_html_in_labels(client) -> None:
	create_watchlist_item(display_name="<script>alert(1)</script>")

	response = client.get("/watchlist")
	body = response.get_data(as_text=True)

	assert "<script>alert(1)</script>" not in body
	assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///C:/Windows/System32/drivers/etc/hosts"])
def test_disallowed_schemes_are_rejected(client, url: str) -> None:
	response = client.post("/watchlist", data={"url": url})
	body = response.get_data(as_text=True)

	assert response.status_code == 200
	assert "Only HTTP and HTTPS marketplace product URLs are allowed." in body
	assert WatchlistItem.query.count() == 0


def test_watchlist_update_page_shows_batch_summary(client, monkeypatch: pytest.MonkeyPatch) -> None:
	create_watchlist_item()

	def fake_update_active_watchlist_items():
		return BatchScrapeResult(
			run_id=77,
			status="completed",
			total=1,
			successful=1,
			failed=0,
			skipped=0,
			started_at=datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
			finished_at=datetime(2026, 9, 1, 10, 1, tzinfo=timezone.utc),
			duration=datetime(2026, 9, 1, 10, 1, tzinfo=timezone.utc) - datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
			item_results=[],
		)

	monkeypatch.setattr("app.routes.update_active_watchlist_items", fake_update_active_watchlist_items)
	response = client.post("/watchlist/update-active")
	body = response.get_data(as_text=True)

	assert response.status_code == 200
	assert "Latest Watchlist Update" in body
	assert "Run ID 77" in body
	assert "Batch update finished." in body
