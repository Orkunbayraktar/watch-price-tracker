"""Tests for the single-product Saat&Saat provider integration."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from app import create_app
from database.db import db, initialize_database
from database.models import Listing, PriceHistory, Product, Seller, WatchlistItem
from scrapers.fetchers import FetchResult
from scrapers.hepsiburada_scraper import HepsiburadaScraper
from scrapers.models import ScrapedProductData
from scrapers.saatvesaat_scraper import SaatVeSaatScraper
from scrapers.trendyol_scraper import TrendyolScraper
from scripts.batch_scrape import build_argument_parser as build_batch_argument_parser
from scripts.smoke_test_common import run_smoke_test
from scripts.smoke_test_live import build_argument_parser as build_smoke_argument_parser
from services.data_quality_service import PLATFORM_OPTIONS
from services.persistence_service import save_scraped_product
from services.scraping_service import SCRAPER_CLASSES, get_platform_for_url, scrape_product
from services.watchlist_service import WatchlistValidationError, create_watchlist_item


PRODUCT_URL = "https://www.saatvesaat.com.tr/skagen-erkek-kol-saati-p-skw6608"
ROOT_HOST_PRODUCT_URL = "https://saatvesaat.com.tr/timex-kol-saati-p-tw2y66700"

FULL_HTML = """
<html>
  <head>
    <meta property="og:title" content="Fallback product title">
    <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Skagen SKW6608 Erkek Kol Saati",
        "brand": {"@type": "Brand", "name": "Skagen"},
        "offers": {
          "@type": "Offer",
          "price": "7.499,90",
          "priceCurrency": "TRY",
          "availability": "https://schema.org/InStock",
          "seller": {"@type": "Organization", "name": "Structured Seller"}
        }
      }
    </script>
  </head>
  <body>
    <h1 class="product-detail-name">Skagen SKW6608 Erkek Kol Saati</h1>
    <span class="product-price-old">8.199,00 TL</span>
    <span class="product-price-new">7.499,90 TL</span>
    <span class="discount-rate">%9</span>
  </body>
</html>
"""

VISIBLE_SELLER_HTML = FULL_HTML.replace(
	"<span class=\"discount-rate\">%9</span>",
	"<span class=\"discount-rate\">%9</span><span data-testid=\"seller-name\">Saat&Saat Online Mağaza</span><span data-testid=\"seller-rating\">4,8</span>",
)

DOM_PRICE_HTML = """
<html>
  <body>
    <h1 class="product-detail-name">Timex TW2Y66700 Kol Saati</h1>
    <s class="product-price-old">9.250,00 TL</s>
    <span class="product-price-new">8.499,90 TL</span>
  </body>
</html>
"""

MINIMAL_HTML = """
<html>
  <body>
    <h1 class="product-detail-name">Skagen SKW6608 Erkek Kol Saati</h1>
    <span class="product-price-new">7.499,90 TL</span>
  </body>
</html>
"""

PRICE_SPECIFICATION_HTML = """
<html>
  <head>
    <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Skagen SKW6608 Erkek Kol Saati",
        "offers": {
          "@type": "AggregateOffer",
          "priceCurrency": "TRY",
          "priceSpecification": {
            "@type": "PriceSpecification",
            "price": "7.499,90"
          }
        }
      }
    </script>
  </head>
</html>
"""


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


def test_url_validation_accepts_observed_product_pattern_on_allowed_hosts() -> None:
	assert SaatVeSaatScraper.is_supported_url(PRODUCT_URL) is True
	assert SaatVeSaatScraper.is_supported_url(ROOT_HOST_PRODUCT_URL) is True
	assert SaatVeSaatScraper.is_supported_url(f"{PRODUCT_URL}?utm_source=test") is True


def test_url_validation_rejects_fake_and_unapproved_hosts() -> None:
	assert SaatVeSaatScraper.is_supported_url("https://fake-saatvesaat.com.tr/skagen-erkek-kol-saati-p-skw6608") is False
	assert SaatVeSaatScraper.is_supported_url("https://cdn.saatvesaat.com.tr/skagen-erkek-kol-saati-p-skw6608") is False


@pytest.mark.parametrize("scheme", ["file", "javascript", "data", "ftp"])
def test_url_validation_rejects_unsafe_schemes(scheme: str) -> None:
	assert SaatVeSaatScraper.is_supported_url(f"{scheme}://www.saatvesaat.com.tr/skagen-erkek-kol-saati-p-skw6608") is False


@pytest.mark.parametrize(
	"url",
	[
		"https://www.saatvesaat.com.tr/",
		"https://www.saatvesaat.com.tr/erkek-saat",
		"https://www.saatvesaat.com.tr/skagen",
		"https://www.saatvesaat.com.tr/arama?q=skagen",
	],
)
def test_url_validation_rejects_non_product_pages(url: str) -> None:
	assert SaatVeSaatScraper.is_supported_url(url) is False


def test_external_product_id_comes_from_product_suffix() -> None:
	assert SaatVeSaatScraper.extract_external_product_id(PRODUCT_URL) == "SKW6608"
	assert SaatVeSaatScraper.extract_external_product_id(ROOT_HOST_PRODUCT_URL) == "TW2Y66700"


def test_robots_denial_prevents_playwright_fetch() -> None:
	robots_manager = Mock()
	parser = Mock()
	parser.can_fetch.return_value = False
	robots_manager.load_rules.return_value = parser
	playwright_fetcher = Mock()
	scraper = SaatVeSaatScraper(
		robots_manager=robots_manager,
		fetchers={"playwright": playwright_fetcher},
	)

	result = scraper.scrape_product(PRODUCT_URL, fetch_strategy="playwright")

	assert result is None
	assert scraper.last_failure_reason == "robots_denied"
	playwright_fetcher.fetch.assert_not_called()


def test_robots_load_failure_prevents_playwright_fetch() -> None:
	robots_manager = Mock()
	robots_manager.load_rules.return_value = None
	playwright_fetcher = Mock()
	scraper = SaatVeSaatScraper(
		robots_manager=robots_manager,
		fetchers={"playwright": playwright_fetcher},
	)

	result = scraper.scrape_product(PRODUCT_URL, fetch_strategy="playwright")

	assert result is None
	assert scraper.last_failure_reason == "robots_load_failed"
	playwright_fetcher.fetch.assert_not_called()


def test_playwright_html_reaches_saatvesaat_parser() -> None:
	robots_manager = Mock()
	parser = Mock()
	parser.can_fetch.return_value = True
	robots_manager.load_rules.return_value = parser
	playwright_fetcher = Mock()
	playwright_fetcher.fetch.return_value = FetchResult(
		html=FULL_HTML,
		final_url=PRODUCT_URL,
		status_code=200,
		page_title="Skagen SKW6608",
	)
	scraper = SaatVeSaatScraper(
		robots_manager=robots_manager,
		fetchers={"playwright": playwright_fetcher},
	)

	result = scraper.scrape_product(PRODUCT_URL, fetch_strategy="playwright")

	assert result is not None
	assert result.platform == "saatvesaat"
	assert result.product_name == "Skagen SKW6608 Erkek Kol Saati"
	playwright_fetcher.fetch.assert_called_once()


def test_json_ld_fields_and_decimal_prices_are_normalized() -> None:
	parsed = SaatVeSaatScraper().parse_product_page(FULL_HTML, PRODUCT_URL)

	assert isinstance(parsed, ScrapedProductData)
	assert parsed.product_name == "Skagen SKW6608 Erkek Kol Saati"
	assert parsed.brand == "Skagen"
	assert parsed.model == "SKW6608"
	assert parsed.current_price == Decimal("7499.90")
	assert parsed.old_price == Decimal("8199.00")
	assert parsed.discount_percentage == Decimal("9")
	assert parsed.currency == "TRY"
	assert parsed.availability == "in_stock"
	assert parsed.external_product_id == "SKW6608"
	assert parsed.platform == "saatvesaat"
	assert parsed.visible_sales_count is None


def test_old_price_is_not_confused_with_dom_current_price() -> None:
	parsed = SaatVeSaatScraper().parse_product_page(DOM_PRICE_HTML, ROOT_HOST_PRODUCT_URL)

	assert parsed is not None
	assert parsed.current_price == Decimal("8499.90")
	assert parsed.old_price == Decimal("9250.00")
	assert parsed.current_price != parsed.old_price


def test_price_specification_is_used_through_shared_json_ld_parser() -> None:
	parsed = SaatVeSaatScraper().parse_product_page(PRICE_SPECIFICATION_HTML, PRODUCT_URL)

	assert parsed is not None
	assert parsed.current_price == Decimal("7499.90")


def test_structured_seller_is_not_used_without_visible_seller_text() -> None:
	parsed = SaatVeSaatScraper().parse_product_page(FULL_HTML, PRODUCT_URL)

	assert parsed is not None
	assert parsed.seller_name is None
	assert parsed.seller_rating is None


def test_visible_seller_is_extracted_when_present() -> None:
	parsed = SaatVeSaatScraper().parse_product_page(VISIBLE_SELLER_HTML, PRODUCT_URL)

	assert parsed is not None
	assert parsed.seller_name == "Saat&Saat Online Mağaza"
	assert parsed.seller_rating == Decimal("4.8")


def test_missing_optional_fields_remain_none() -> None:
	parsed = SaatVeSaatScraper().parse_product_page(MINIMAL_HTML, PRODUCT_URL)

	assert parsed is not None
	assert parsed.brand is None
	assert parsed.old_price is None
	assert parsed.discount_percentage is None
	assert parsed.seller_name is None
	assert parsed.seller_rating is None
	assert parsed.availability is None
	assert parsed.visible_sales_count is None


def test_scraper_registry_selects_saatvesaat_without_changing_existing_providers() -> None:
	assert get_platform_for_url(PRODUCT_URL) == "saatvesaat"
	assert get_platform_for_url("https://www.trendyol.com/casio/f-91w-p-1001") == "trendyol"
	assert get_platform_for_url("https://www.hepsiburada.com/casio-retro-pm-sacsa159wan1df") == "hepsiburada"
	assert TrendyolScraper in SCRAPER_CLASSES
	assert HepsiburadaScraper in SCRAPER_CLASSES
	assert SaatVeSaatScraper in SCRAPER_CLASSES


def test_scraping_service_dispatches_to_saatvesaat_provider() -> None:
	expected = Mock(spec=ScrapedProductData)
	with patch.object(SaatVeSaatScraper, "scrape_product", return_value=expected) as scrape_mock:
		result = scrape_product(PRODUCT_URL)

	assert result is expected
	scrape_mock.assert_called_once_with(PRODUCT_URL)


def test_watchlist_accepts_saatvesaat_and_rejects_duplicate(app_context) -> None:
	item = create_watchlist_item(PRODUCT_URL)

	assert item.platform == "saatvesaat"
	assert item.external_product_id == "SKW6608"
	with pytest.raises(WatchlistValidationError, match="already being tracked"):
		create_watchlist_item(PRODUCT_URL)
	assert WatchlistItem.query.count() == 1


def test_generic_persistence_supports_visible_seller(app_context) -> None:
	parsed = SaatVeSaatScraper().parse_product_page(VISIBLE_SELLER_HTML, PRODUCT_URL)

	assert parsed is not None
	result = save_scraped_product(parsed)

	assert result is not None
	assert Product.query.count() == 1
	assert Seller.query.count() == 1
	assert Listing.query.one().platform == "saatvesaat"
	assert PriceHistory.query.count() == 1


def test_sellerless_persistence_and_second_scrape_reuse_records(app_context) -> None:
	first = SaatVeSaatScraper().parse_product_page(MINIMAL_HTML, PRODUCT_URL)
	second = SaatVeSaatScraper().parse_product_page(MINIMAL_HTML, PRODUCT_URL)

	assert first is not None
	assert second is not None
	first_result = save_scraped_product(first)
	second_result = save_scraped_product(second)

	assert first_result is not None
	assert second_result is not None
	assert Product.query.count() == 1
	assert Seller.query.count() == 0
	assert Listing.query.count() == 1
	assert Listing.query.one().seller_id is None
	assert PriceHistory.query.count() == 2
	assert first_result.listing.id == second_result.listing.id


def test_control_center_and_data_quality_recognize_saatvesaat(app_context) -> None:
	item = WatchlistItem(
		platform="saatvesaat",
		url=PRODUCT_URL,
		external_product_id="SKW6608",
		is_active=True,
	)
	db.session.add(item)
	db.session.commit()

	with app_context.test_client() as client:
		body = client.get("/scraping").get_data(as_text=True)

	assert "Saat&amp;Saat" in body
	assert "platform-badge--saatvesaat" in body
	assert ("saatvesaat", "Saat&Saat") in PLATFORM_OPTIONS


def test_live_and_batch_cli_accept_saatvesaat() -> None:
	smoke_args = build_smoke_argument_parser().parse_args(
		["--platform", "saatvesaat", "--fetcher", "playwright", PRODUCT_URL]
	)
	batch_args = build_batch_argument_parser().parse_args(
		["urls.txt", "--platform", "saatvesaat", "--fetcher", "playwright"]
	)

	assert smoke_args.platform == "saatvesaat"
	assert batch_args.platform == "saatvesaat"


def test_smoke_failure_output_uses_canonical_platform_id(capsys: pytest.CaptureFixture[str]) -> None:
	exit_code = run_smoke_test(
		"https://example.com/not-supported",
		SaatVeSaatScraper,
		"Saat&Saat",
		fetch_strategy="playwright",
		label="Live smoke test",
	)

	assert exit_code == 2
	assert "Platform: saatvesaat" in capsys.readouterr().out
