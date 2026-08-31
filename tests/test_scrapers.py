"""Tests for the single-product Trendyol scraping proof of concept."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import Mock, patch

import requests

from database.db import db
from scrapers.models import ScrapedProductData
from scrapers.trendyol_scraper import TrendyolScraper
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
          "price": "7.499,90",
          "priceCurrency": "TRY",
          "availability": "https://schema.org/InStock",
          "seller": {
            "@type": "Organization",
            "name": "Example Watch Store"
          }
        }
      }
    </script>
  </head>
  <body>
    <h1 data-testid="product-name">Casio G-SHOCK GA-2100-1A1DR Erkek Kol Saati</h1>
    <span data-testid="price-current-price">7.499,90 TL</span>
    <span data-testid="price-old-price">8.199,90 TL</span>
    <span data-testid="discount-rate">8%</span>
    <span data-testid="seller-name">Example Watch Store</span>
    <span data-testid="seller-rating">4,8</span>
  </body>
</html>
"""

MINIMAL_HTML = """
<html>
  <body>
    <h1 data-testid="product-name">Casio G-SHOCK Minimal Product</h1>
    <span data-testid="price-current-price">6.199 TL</span>
  </body>
</html>
"""

MISSING_NAME_HTML = """
<html>
	<body>
		<span data-testid="price-current-price">6.199 TL</span>
	</body>
</html>
"""

MISSING_PRICE_HTML = """
<html>
	<body>
		<h1 data-testid="product-name">Casio G-SHOCK Minimal Product</h1>
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


def test_trendyol_url_validation_accepts_supported_hosts() -> None:
	assert TrendyolScraper.is_supported_url("https://www.trendyol.com/casio/g-shock-ga-2100-p-33139591") is True
	assert TrendyolScraper.is_supported_url("https://trendyol.com/casio/g-shock-ga-2100-p-33139591") is True


def test_trendyol_url_validation_rejects_fake_domain() -> None:
	assert TrendyolScraper.is_supported_url("https://fake-trendyol.com/casio/g-shock-ga-2100-p-33139591") is False


def test_external_product_id_is_extracted() -> None:
	assert TrendyolScraper.extract_external_product_id(
		"https://www.trendyol.com/casio/g-shock-ga-2100-p-33139591?boutiqueId=61"
	) == "33139591"


def test_turkish_money_is_normalized_to_decimal() -> None:
	assert TrendyolScraper.normalize_money("6.199 TL") == Decimal("6199.00")
	assert TrendyolScraper.normalize_money("7.499,90 TL") == Decimal("7499.90")


def test_generic_decimal_normalization_handles_turkish_and_standard_formats() -> None:
	assert TrendyolScraper.normalize_decimal("4.8") == Decimal("4.8")
	assert TrendyolScraper.normalize_decimal("4,8") == Decimal("4.8")
	assert TrendyolScraper.normalize_decimal("%15") == Decimal("15")
	assert TrendyolScraper.normalize_decimal("15,5%") == Decimal("15.5")


def test_parse_product_page_reads_name_and_price() -> None:
	scraper = TrendyolScraper()

	parsed = scraper.parse_product_page(
		FULL_HTML,
		"https://www.trendyol.com/casio/g-shock-ga-2100-p-33139591",
	)

	assert parsed is not None
	assert isinstance(parsed, ScrapedProductData)
	assert parsed.product_name == "Casio G-SHOCK GA-2100-1A1DR Erkek Kol Saati"
	assert parsed.current_price == Decimal("7499.90")
	assert parsed.brand == "Casio"
	assert parsed.model == "GA-2100-1A1DR"
	assert parsed.external_product_id == "33139591"


def test_parse_product_page_reads_seller_fields_when_available() -> None:
	scraper = TrendyolScraper()

	parsed = scraper.parse_product_page(
		FULL_HTML,
		"https://www.trendyol.com/casio/g-shock-ga-2100-p-33139591",
	)

	assert parsed is not None
	assert parsed.seller_name == "Example Watch Store"
	assert parsed.seller_rating == Decimal("4.8")
	assert parsed.currency == "TRY"
	assert parsed.availability == "in_stock"


def test_missing_optional_fields_return_none() -> None:
	scraper = TrendyolScraper()

	parsed = scraper.parse_product_page(
		MINIMAL_HTML,
		"https://www.trendyol.com/casio/g-shock-minimal-p-33139591",
	)

	assert parsed is not None
	assert parsed.old_price is None
	assert parsed.discount_percentage is None
	assert parsed.seller_name is None
	assert parsed.seller_rating is None
	assert parsed.availability is None
	assert parsed.visible_sales_count is None


def test_no_http_request_is_sent_when_robots_denies_access() -> None:
	robots_manager = Mock()
	robots_manager.can_fetch.return_value = False
	session = requests.Session()
	session.get = Mock()
	scraper = TrendyolScraper(robots_manager=robots_manager, session=session)

	result = scraper.scrape_product("https://www.trendyol.com/casio/g-shock-ga-2100-p-33139591")

	assert result is None
	session.get.assert_not_called()


def test_http_error_is_handled_without_crashing() -> None:
	robots_manager = Mock()
	robots_manager.can_fetch.return_value = True
	session = requests.Session()
	session.get = Mock(return_value=FakeResponse("", requests.HTTPError("403 Client Error")))
	scraper = TrendyolScraper(robots_manager=robots_manager, session=session)

	result = scraper.scrape_product("https://www.trendyol.com/casio/g-shock-ga-2100-p-33139591")

	assert result is None
	session.get.assert_called_once()


def test_parse_returns_none_when_product_name_is_missing() -> None:
	scraper = TrendyolScraper()

	parsed = scraper.parse_product_page(
		MISSING_NAME_HTML,
		"https://www.trendyol.com/casio/g-shock-minimal-p-33139591",
	)

	assert parsed is None


def test_parse_returns_none_when_current_price_is_missing() -> None:
	scraper = TrendyolScraper()

	parsed = scraper.parse_product_page(
		MISSING_PRICE_HTML,
		"https://www.trendyol.com/casio/g-shock-minimal-p-33139591",
	)

	assert parsed is None


def test_parse_returns_none_when_external_product_id_is_missing() -> None:
	scraper = TrendyolScraper()

	parsed = scraper.parse_product_page(
		FULL_HTML,
		"https://www.trendyol.com/casio/g-shock-ga-2100",
	)

	assert parsed is None


def test_parser_does_not_access_database() -> None:
	scraper = TrendyolScraper()
	with patch.object(db.session, "add") as add_mock:
		parsed = scraper.parse_product_page(
			FULL_HTML,
			"https://www.trendyol.com/casio/g-shock-ga-2100-p-33139591",
		)

	assert parsed is not None
	assert isinstance(parsed, ScrapedProductData)
	add_mock.assert_not_called()


def test_scraping_service_returns_scraper_result_for_supported_url() -> None:
	scraper = Mock()
	scraper.is_supported_url.return_value = True
	expected = Mock(spec=ScrapedProductData)
	scraper.scrape_product.return_value = expected

	result = scrape_product("https://www.trendyol.com/casio/g-shock-ga-2100-p-33139591", scraper=scraper)

	assert result is expected
	scraper.scrape_product.assert_called_once_with(
		"https://www.trendyol.com/casio/g-shock-ga-2100-p-33139591"
	)


def test_scraping_service_rejects_unsupported_url() -> None:
	scraper = Mock()
	scraper.is_supported_url.return_value = False

	result = scrape_product("https://fake-trendyol.com/casio/g-shock-ga-2100-p-33139591", scraper=scraper)

	assert result is None
	scraper.scrape_product.assert_not_called()
