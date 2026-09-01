"""Tests for the single-product Hepsiburada scraping proof of concept."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import Mock, patch

import requests

from app import create_app
from database.db import db, initialize_database
from database.models import Listing, PriceHistory, Product, Seller
from scrapers.hepsiburada_scraper import HepsiburadaScraper
from scrapers.models import ScrapedProductData
from services.persistence_service import save_scraped_product
from services.scraping_service import scrape_product


FULL_HTML = """
<html>
  <head>
    <meta property="og:title" content="Casio G-SHOCK GA-2100-1A1DR Erkek Kol Saati">
    <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Casio G-SHOCK GA-2100-1A1DR Erkek Kol Saati",
        "brand": {
          "@type": "Brand",
          "name": "Casio"
        },
        "offers": {
          "@type": "Offer",
          "price": "6.999,00",
          "priceCurrency": "TRY",
          "availability": "https://schema.org/InStock",
          "seller": {
            "@type": "Organization",
            "name": "Example HB Store"
          }
        }
      }
    </script>
  </head>
  <body>
    <h1 data-test-id="product-name">Casio G-SHOCK GA-2100-1A1DR Erkek Kol Saati</h1>
    <span data-test-id="price-current-price">6.999,00 TL</span>
    <span data-test-id="price-old-price">7.499,90 TL</span>
    <span data-test-id="discount-rate">7%</span>
		<span data-test-id="seller-name">Example HB Store</span>
    <span data-test-id="seller-rating">4,7</span>
  </body>
</html>
"""

RENDERED_NAME_HTML = """
<html>
	<head>
		<meta property="og:title" content="Fallback Title Should Not Win">
		<meta itemprop="price" content="7499.90">
	</head>
	<body>
		<h1 data-test-id="product-title">Casio Vintage A159WA-N1DF Kol Saati</h1>
	</body>
</html>
"""

RENDERED_PRICE_HTML = """
<html>
	<body>
		<h1 data-test-id="product-name">Casio Vintage A159WA-N1DF Kol Saati</h1>
		<div class="price old-price">8.199,00 TL</div>
		<span data-test-id="price-current-price">7.499,90 TL</span>
		<span data-test-id="price-old-price">8.199,00 TL</span>
	</body>
</html>
"""

JSON_LD_AGGREGATE_OFFER_HTML = """
<html>
	<head>
		<script type="application/ld+json">
			{
				"@context": "https://schema.org",
				"@type": "Product",
				"name": "Casio Vintage A159WA-N1DF Kol Saati",
				"brand": {"@type": "Brand", "name": "Casio"},
				"offers": {
					"@type": "AggregateOffer",
					"lowPrice": "7.499,90",
					"highPrice": "8.199,00",
					"priceCurrency": "TRY",
					"availability": "https://schema.org/InStock",
					"priceSpecification": {
						"@type": "PriceSpecification",
						"price": "7.499,90"
					}
				}
			}
		</script>
	</head>
	<body>
		<h1>Fallback Casio Name</h1>
	</body>
</html>
"""

STRUCTURED_SELLER_ONLY_HTML = """
<html>
	<head>
		<script type="application/ld+json">
			{
				"@context": "https://schema.org",
				"@type": "Product",
				"name": "Casio G-SHOCK Structured Seller Test",
				"offers": {
					"@type": "Offer",
					"price": "6.499,00",
					"priceCurrency": "TRY",
					"seller": {
						"@type": "Organization",
						"name": "Invisible Structured Seller"
					}
				}
			}
		</script>
	</head>
	<body>
		<h1 data-test-id="product-name">Casio G-SHOCK Structured Seller Test</h1>
		<span data-test-id="price-current-price">6.499,00 TL</span>
	</body>
</html>
"""

MINIMAL_HTML = """
<html>
  <body>
    <h1 data-test-id="product-name">Casio G-SHOCK Minimal Product</h1>
    <span data-test-id="price-current-price">6.999 TL</span>
  </body>
</html>
"""


class FakeResponse:
	"""Simple fake response object for session.get tests."""

	def __init__(self, text: str, status_error: Exception | None = None) -> None:
		self.text = text
		self._status_error = status_error

	def raise_for_status(self) -> None:
		if self._status_error is not None:
			raise self._status_error


def test_hepsiburada_url_validation_accepts_supported_hosts() -> None:
	assert HepsiburadaScraper.is_supported_url("https://www.hepsiburada.com/casio-g-shock-ga-2100-p-HBCV0000123456") is True
	assert HepsiburadaScraper.is_supported_url("https://hepsiburada.com/casio-g-shock-ga-2100-p-HBCV0000123456") is True
	assert HepsiburadaScraper.is_supported_url("https://www.hepsiburada.com/casio-retro-kol-saati-a159wa-n1df-pm-sacsa159wan1df") is True
	assert HepsiburadaScraper.is_supported_url("https://hepsiburada.com/casio-retro-kol-saati-a159wa-n1df-pm-sacsa159wan1df") is True


def test_hepsiburada_url_validation_accepts_query_parameters_on_product_urls() -> None:
	assert (
		HepsiburadaScraper.is_supported_url(
			"https://www.hepsiburada.com/casio-retro-kol-saati-a159wa-n1df-pm-sacsa159wan1df?magaza=demo&merchantId=42"
		)
		is True
	)


def test_hepsiburada_url_validation_rejects_fake_domain() -> None:
	assert HepsiburadaScraper.is_supported_url("https://fake-hepsiburada.com/casio-g-shock-ga-2100-p-HBCV0000123456") is False
	assert HepsiburadaScraper.is_supported_url("https://fake-hepsiburada.com/casio-retro-kol-saati-a159wa-n1df-pm-sacsa159wan1df") is False


def test_hepsiburada_url_validation_rejects_non_product_pages() -> None:
	assert HepsiburadaScraper.is_supported_url("https://www.hepsiburada.com/kampanyalar") is False
	assert HepsiburadaScraper.is_supported_url("https://www.hepsiburada.com/ara?q=casio") is False


def test_hepsiburada_pm_url_extracts_external_product_id() -> None:
	assert (
		HepsiburadaScraper.extract_external_product_id(
			"https://www.hepsiburada.com/casio-retro-kol-saati-a159wa-n1df-pm-sacsa159wan1df"
		)
		== "SACSA159WAN1DF"
	)
	assert (
		HepsiburadaScraper.extract_external_product_id(
			"https://www.hepsiburada.com/casio-retro-kol-saati-a159wa-n1df-pm-sacsa159wan1df?magaza=demo&merchantId=42"
		)
		== "SACSA159WAN1DF"
	)


def test_hepsiburada_robots_denial_prevents_request() -> None:
	robots_manager = Mock()
	parser = Mock()
	parser.can_fetch.return_value = False
	robots_manager.load_rules.return_value = parser
	session = requests.Session()
	session.get = Mock()
	scraper = HepsiburadaScraper(robots_manager=robots_manager, session=session)

	result = scraper.scrape_product("https://www.hepsiburada.com/casio-g-shock-ga-2100-p-HBCV0000123456")

	assert result is None
	assert scraper.last_failure_reason == "robots_denied"
	session.get.assert_not_called()


def test_hepsiburada_http_403_becomes_http_forbidden() -> None:
	robots_manager = Mock()
	parser = Mock()
	parser.can_fetch.return_value = True
	robots_manager.load_rules.return_value = parser
	session = requests.Session()
	http_error = requests.HTTPError("403 Client Error")
	http_error.response = Mock(status_code=403)
	session.get = Mock(return_value=FakeResponse("", http_error))
	scraper = HepsiburadaScraper(robots_manager=robots_manager, session=session)

	result = scraper.scrape_product("https://www.hepsiburada.com/casio-g-shock-ga-2100-p-HBCV0000123456")

	assert result is None
	assert scraper.last_failure_reason == "http_forbidden"


def test_hepsiburada_http_429_becomes_rate_limited() -> None:
	robots_manager = Mock()
	parser = Mock()
	parser.can_fetch.return_value = True
	robots_manager.load_rules.return_value = parser
	session = requests.Session()
	http_error = requests.HTTPError("429 Client Error")
	http_error.response = Mock(status_code=429)
	session.get = Mock(return_value=FakeResponse("", http_error))
	scraper = HepsiburadaScraper(robots_manager=robots_manager, session=session)

	result = scraper.scrape_product("https://www.hepsiburada.com/casio-g-shock-ga-2100-p-HBCV0000123456")

	assert result is None
	assert scraper.last_failure_reason == "rate_limited"


def test_hepsiburada_timeout_sets_timeout_failure() -> None:
	robots_manager = Mock()
	parser = Mock()
	parser.can_fetch.return_value = True
	robots_manager.load_rules.return_value = parser
	session = requests.Session()
	session.get = Mock(side_effect=requests.Timeout("timed out"))
	scraper = HepsiburadaScraper(robots_manager=robots_manager, session=session)

	result = scraper.scrape_product("https://www.hepsiburada.com/casio-g-shock-ga-2100-p-HBCV0000123456")

	assert result is None
	assert scraper.last_failure_reason == "timeout"


def test_hepsiburada_parse_reads_json_ld_name_and_price() -> None:
	scraper = HepsiburadaScraper()

	parsed = scraper.parse_product_page(
		FULL_HTML,
		"https://www.hepsiburada.com/casio-g-shock-ga-2100-p-HBCV0000123456",
	)

	assert parsed is not None
	assert isinstance(parsed, ScrapedProductData)
	assert parsed.product_name == "Casio G-SHOCK GA-2100-1A1DR Erkek Kol Saati"
	assert parsed.current_price == Decimal("6999.00")
	assert parsed.platform == "hepsiburada"
	assert parsed.external_product_id == "HBCV0000123456"


def test_hepsiburada_parse_reads_rendered_product_name() -> None:
	scraper = HepsiburadaScraper()

	parsed = scraper.parse_product_page(
		RENDERED_NAME_HTML,
		"https://www.hepsiburada.com/casio-vintage-a159wa-n1df-pm-sacsa159wan1df",
	)

	assert parsed is not None
	assert parsed.product_name == "Casio Vintage A159WA-N1DF Kol Saati"
	assert parsed.current_price == Decimal("7499.90")


def test_hepsiburada_parse_reads_rendered_current_price() -> None:
	scraper = HepsiburadaScraper()

	parsed = scraper.parse_product_page(
		RENDERED_PRICE_HTML,
		"https://www.hepsiburada.com/casio-vintage-a159wa-n1df-pm-sacsa159wan1df",
	)

	assert parsed is not None
	assert parsed.current_price == Decimal("7499.90")
	assert parsed.old_price == Decimal("8199.00")


def test_hepsiburada_parse_reads_json_ld_aggregate_offer_price() -> None:
	scraper = HepsiburadaScraper()

	parsed = scraper.parse_product_page(
		JSON_LD_AGGREGATE_OFFER_HTML,
		"https://www.hepsiburada.com/casio-vintage-a159wa-n1df-pm-sacsa159wan1df",
	)

	assert parsed is not None
	assert parsed.current_price == Decimal("7499.90")
	assert parsed.currency == "TRY"
	assert parsed.availability == "in_stock"


def test_hepsiburada_parse_reads_seller_when_available() -> None:
	scraper = HepsiburadaScraper()

	parsed = scraper.parse_product_page(
		FULL_HTML,
		"https://www.hepsiburada.com/casio-g-shock-ga-2100-p-HBCV0000123456",
	)

	assert parsed is not None
	assert parsed.seller_name == "Example HB Store"
	assert parsed.seller_rating == Decimal("4.7")
	assert parsed.brand == "Casio"
	assert parsed.currency == "TRY"
	assert parsed.availability == "in_stock"


def test_hepsiburada_structured_seller_is_ignored_when_not_visible() -> None:
	scraper = HepsiburadaScraper()

	parsed = scraper.parse_product_page(
		STRUCTURED_SELLER_ONLY_HTML,
		"https://www.hepsiburada.com/casio-g-shock-structured-seller-test-p-HBCV0000123456",
	)

	assert parsed is not None
	assert parsed.seller_name is None


def test_hepsiburada_missing_optional_fields_return_none() -> None:
	scraper = HepsiburadaScraper()

	parsed = scraper.parse_product_page(
		MINIMAL_HTML,
		"https://www.hepsiburada.com/casio-g-shock-minimal-p-HBCV0000123456",
	)

	assert parsed is not None
	assert parsed.old_price is None
	assert parsed.discount_percentage is None
	assert parsed.seller_name is None
	assert parsed.seller_rating is None
	assert parsed.availability is None
	assert parsed.visible_sales_count is None


def test_hepsiburada_old_price_is_not_mistaken_for_current_price() -> None:
	scraper = HepsiburadaScraper()

	parsed = scraper.parse_product_page(
		RENDERED_PRICE_HTML,
		"https://www.hepsiburada.com/casio-vintage-a159wa-n1df-pm-sacsa159wan1df",
	)

	assert parsed is not None
	assert parsed.current_price == Decimal("7499.90")
	assert parsed.current_price != parsed.old_price


def test_scraping_service_selects_hepsiburada_scraper_from_registry() -> None:
	expected = Mock(spec=ScrapedProductData)
	with patch.object(HepsiburadaScraper, "scrape_product", return_value=expected) as scrape_mock:
		result = scrape_product("https://www.hepsiburada.com/casio-g-shock-ga-2100-p-HBCV0000123456")

	assert result is expected
	scrape_mock.assert_called_once_with("https://www.hepsiburada.com/casio-g-shock-ga-2100-p-HBCV0000123456")


class HepsiburadaPersistenceTests(unittest.TestCase):
	"""Verify the generic persistence layer accepts Hepsiburada DTO data."""

	def setUp(self) -> None:
		self.app = create_app(
			{
				"TESTING": True,
				"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
			}
		)
		self.app_context = self.app.app_context()
		self.app_context.push()
		initialize_database(self.app)

	def tearDown(self) -> None:
		db.session.remove()
		db.drop_all()
		db.engine.dispose()
		self.app_context.pop()

	def test_hepsiburada_scraped_data_persists_with_generic_service(self) -> None:
		scraper = HepsiburadaScraper()
		parsed = scraper.parse_product_page(
			FULL_HTML,
			"https://www.hepsiburada.com/casio-g-shock-ga-2100-p-HBCV0000123456",
		)

		self.assertIsNotNone(parsed)
		result = save_scraped_product(parsed)

		self.assertIsNotNone(result)
		self.assertEqual(Product.query.count(), 1)
		self.assertEqual(Seller.query.count(), 1)
		self.assertEqual(Listing.query.count(), 1)
		self.assertEqual(PriceHistory.query.count(), 1)
		self.assertEqual(Listing.query.one().platform, "hepsiburada")
		self.assertEqual(Listing.query.one().external_product_id, "HBCV0000123456")

	def test_hepsiburada_sellerless_listing_persists_with_generic_service(self) -> None:
		scraper = HepsiburadaScraper()
		parsed = scraper.parse_product_page(
			MINIMAL_HTML,
			"https://www.hepsiburada.com/casio-g-shock-minimal-p-HBCV0000123456",
		)

		self.assertIsNotNone(parsed)
		result = save_scraped_product(parsed)

		self.assertIsNotNone(result)
		self.assertEqual(Product.query.count(), 1)
		self.assertEqual(Seller.query.count(), 0)
		self.assertEqual(Listing.query.count(), 1)
		self.assertEqual(PriceHistory.query.count(), 1)
		self.assertIsNone(Listing.query.one().seller_id)

	def test_hepsiburada_second_persistence_reuses_listing_and_increments_history(self) -> None:
		scraper = HepsiburadaScraper()
		first = scraper.parse_product_page(
			MINIMAL_HTML,
			"https://www.hepsiburada.com/casio-g-shock-minimal-p-HBCV0000123456",
		)
		second = scraper.parse_product_page(
			MINIMAL_HTML,
			"https://www.hepsiburada.com/casio-g-shock-minimal-p-HBCV0000123456",
		)

		self.assertIsNotNone(first)
		self.assertIsNotNone(second)
		first_result = save_scraped_product(first)
		second_result = save_scraped_product(second)

		self.assertIsNotNone(first_result)
		self.assertIsNotNone(second_result)
		self.assertEqual(Product.query.count(), 1)
		self.assertEqual(Seller.query.count(), 0)
		self.assertEqual(Listing.query.count(), 1)
		self.assertEqual(PriceHistory.query.count(), 2)
		self.assertEqual(first_result.listing.id, second_result.listing.id)