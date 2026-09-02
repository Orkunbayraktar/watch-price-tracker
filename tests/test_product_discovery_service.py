"""Service and temporary-state tests for product discovery."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest

from app import create_app
from database.db import db, initialize_database
from database.models import Listing, PriceHistory, Product, WatchlistItem
from discovery import DiscoveredProduct, DiscoveryResult
from services.discovery_preview_store import DiscoveryPreviewError, DiscoveryPreviewStore
from services.product_discovery_service import add_discovered_products, discover_products, get_tracked_discovery_urls


URL_1 = "https://www.trendyol.com/casio/f-91w-p-1001"
URL_2 = "https://www.trendyol.com/casio/ga-2100-p-1002"


@pytest.fixture
def app_context(tmp_path: Path):
	app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "DISCOVERY_STATE_DIR": tmp_path})
	with app.app_context():
		initialize_database(app)
		yield app
		db.session.remove()
		db.drop_all()
		db.engine.dispose()


def result_with_products() -> DiscoveryResult:
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
	)


def test_service_deduplicates_provider_results_and_applies_limit() -> None:
	provider = Mock(platform="trendyol")
	provider.discover.return_value = DiscoveryResult(
		platform="trendyol",
		brand="Casio",
		status="success",
		source_url="https://www.trendyol.com/sr?q=Casio",
		products=(
			DiscoveredProduct("trendyol", URL_1),
			DiscoveredProduct("trendyol", URL_1 + "#reviews"),
			DiscoveredProduct("trendyol", URL_2),
		),
	)

	result = discover_products("trendyol", "Casio", max_products=1, max_pages=3, provider=provider)

	assert result.discovered_count == 1
	provider.discover.assert_called_once_with("Casio", max_pages=3, max_products=1)


def test_discovery_metadata_does_not_create_price_history(app_context) -> None:
	provider = Mock(platform="trendyol")
	provider.discover.return_value = result_with_products()

	result = discover_products("trendyol", "Casio", max_products=20, max_pages=1, provider=provider)

	assert result.products[0].current_price == Decimal("799.90")
	assert Product.query.count() == 0
	assert Listing.query.count() == 0
	assert PriceHistory.query.count() == 0


def test_selected_products_use_watchlist_service_and_duplicates_are_safe(app_context, monkeypatch: pytest.MonkeyPatch) -> None:
	from services import product_discovery_service

	calls: list[str] = []
	real_create = product_discovery_service.create_watchlist_item

	def tracking_create(url: str, display_name: str | None = None):
		calls.append(url)
		return real_create(url, display_name)

	monkeypatch.setattr(product_discovery_service, "create_watchlist_item", tracking_create)
	first = add_discovered_products(result_with_products(), [URL_1, URL_2])
	second = add_discovered_products(result_with_products(), [URL_1, URL_1, "https://evil.example/product"])

	assert first.added_count == 2
	assert second.added_count == 0
	assert second.already_tracked == 1
	assert second.skipped == 1
	assert calls == [URL_1, URL_2, URL_1]
	assert WatchlistItem.query.count() == 2


def test_no_selected_products_is_safe(app_context) -> None:
	result = add_discovered_products(result_with_products(), [])

	assert result.added_count == 0
	assert WatchlistItem.query.count() == 0


def test_already_tracked_urls_use_watchlist_canonical_values(app_context) -> None:
	db.session.add(WatchlistItem(platform="trendyol", url=URL_1, external_product_id="1001"))
	db.session.commit()

	assert get_tracked_discovery_urls(result_with_products().products) == {URL_1}


def test_preview_store_round_trips_decimal_without_cookie_payload(tmp_path: Path) -> None:
	store = DiscoveryPreviewStore(tmp_path, "test-secret", ttl_seconds=60)

	token = store.save(result_with_products())
	loaded = store.load(token)

	assert "Casio F-91W" not in token
	assert loaded == result_with_products()
	assert len(list(tmp_path.glob("*.json"))) == 1


def test_preview_store_rejects_tampered_token(tmp_path: Path) -> None:
	store = DiscoveryPreviewStore(tmp_path, "test-secret", ttl_seconds=60)
	token = store.save(result_with_products())

	with pytest.raises(DiscoveryPreviewError):
		store.load(token + "tampered")
