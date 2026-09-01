"""Tests for explicit fetch strategies and Playwright feasibility integration."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
import types
from unittest.mock import Mock, patch

import pytest

from app import create_app
from database.db import db, initialize_database
from database.models import Listing, PriceHistory, Product, Seller
from scrapers.fetchers.base import FetchResult, FetcherError
from scrapers.hepsiburada_scraper import HepsiburadaScraper
from scrapers.trendyol_scraper import TrendyolScraper
from services.scraping_service import create_scraper_for_url, scrape_and_save_product, scrape_product
from scrapers.fetchers.playwright_fetcher import _run_playwright_fetch


TRENDYOL_URL = "https://www.trendyol.com/casio/g-shock-ga-2100-p-33139591"
HEPSIBURADA_URL = "https://www.hepsiburada.com/casio-retro-kol-saati-a159wa-n1df-pm-sacsa159wan1df"

TRENDYOL_HTML = """
<html>
  <head>
    <title>Casio Product</title>
    <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Casio G-SHOCK GA-2100-1A1DR Erkek Kol Saati",
        "brand": {"@type": "Brand", "name": "Casio"},
        "offers": {
          "@type": "Offer",
          "price": "7.499,90",
          "priceCurrency": "TRY",
          "availability": "https://schema.org/InStock",
          "seller": {"@type": "Organization", "name": "Example Watch Store"}
        }
      }
    </script>
  </head>
  <body>
    <h1 data-testid="product-name">Casio G-SHOCK GA-2100-1A1DR Erkek Kol Saati</h1>
    <span data-testid="price-current-price">7.499,90 TL</span>
    <span data-testid="seller-name">Example Watch Store</span>
    <span data-testid="seller-rating">4,8</span>
  </body>
</html>
"""

HEPSIBURADA_HTML = """
<html>
  <head>
    <title>Hepsiburada Product</title>
    <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Casio Retro Kol Saati A159WA",
        "brand": {"@type": "Brand", "name": "Casio"},
        "offers": {
					"@type": "AggregateOffer",
					"lowPrice": "7.499,90",
					"highPrice": "8.199,00",
          "priceCurrency": "TRY",
					"availability": "https://schema.org/InStock"
        }
      }
    </script>
  </head>
  <body>
    <h1 data-test-id="product-name">Casio Retro Kol Saati A159WA</h1>
		<span data-test-id="price-old-price">8.199,00 TL</span>
		<span data-test-id="price-current-price">7.499,90 TL</span>
  </body>
</html>
"""

CHALLENGE_HTML = """
<html>
  <head><title>Access Denied</title></head>
  <body>Forbidden. CAPTCHA required because of unusual traffic.</body>
</html>
"""


class FakeFetcher:
	"""Simple fetcher stub for explicit strategy tests."""

	def __init__(self, result: FetchResult | None = None, error: FetcherError | None = None) -> None:
		self.result = result
		self.error = error
		self.calls: list[dict[str, object]] = []

	def fetch(
		self,
		url: str,
		*,
		user_agent: str,
		timeout_seconds: int | float,
		headless: bool = True,
	) -> FetchResult:
		self.calls.append(
			{
				"url": url,
				"user_agent": user_agent,
				"timeout_seconds": timeout_seconds,
				"headless": headless,
			}
		)
		if self.error is not None:
			raise self.error
		if self.result is None:
			raise AssertionError("FakeFetcher requires either a result or an error.")
		return self.result


class FakePlaywrightTimeoutError(Exception):
	"""Fake Playwright timeout error used by lifecycle tests."""


class FakePlaywrightError(Exception):
	"""Fake Playwright generic error used by lifecycle tests."""


class FakePage:
	def __init__(
		self,
		events: list[str],
		*,
		html: str,
		final_url: str,
		page_title: str,
		goto_error: Exception | None = None,
		close_error: Exception | None = None,
		playwright_state: dict[str, bool],
	) -> None:
		self._events = events
		self._html = html
		self._final_url = final_url
		self._page_title = page_title
		self._goto_error = goto_error
		self._close_error = close_error
		self._playwright_state = playwright_state

	def goto(self, url: str, wait_until: str, timeout: int):
		self._events.append(f"goto:{url}:{wait_until}:{timeout}")
		if self._goto_error is not None:
			raise self._goto_error
		return types.SimpleNamespace(status=200)

	def content(self) -> str:
		self._events.append("content")
		return self._html

	def title(self) -> str:
		self._events.append("title")
		return self._page_title

	@property
	def url(self) -> str:
		return self._final_url

	def close(self) -> None:
		self._events.append("page.close")
		if self._playwright_state["stopped"]:
			raise AssertionError("Page was closed after Playwright stopped")
		if self._close_error is not None:
			raise self._close_error


class FakeContext:
	def __init__(self, events: list[str], page: FakePage, playwright_state: dict[str, bool], close_error: Exception | None = None) -> None:
		self._events = events
		self._page = page
		self._playwright_state = playwright_state
		self._close_error = close_error

	def new_page(self) -> FakePage:
		self._events.append("context.new_page")
		return self._page

	def close(self) -> None:
		self._events.append("context.close")
		if self._playwright_state["stopped"]:
			raise AssertionError("Context was closed after Playwright stopped")
		if self._close_error is not None:
			raise self._close_error


class FakeBrowser:
	def __init__(self, events: list[str], context: FakeContext, playwright_state: dict[str, bool], close_error: Exception | None = None) -> None:
		self._events = events
		self._context = context
		self._playwright_state = playwright_state
		self._close_error = close_error

	def new_context(self, user_agent: str) -> FakeContext:
		self._events.append(f"browser.new_context:{user_agent}")
		return self._context

	def close(self) -> None:
		self._events.append("browser.close")
		if self._playwright_state["stopped"]:
			raise AssertionError("Browser was closed after Playwright stopped")
		if self._close_error is not None:
			raise self._close_error


class FakeChromium:
	def __init__(self, events: list[str], browser: FakeBrowser) -> None:
		self._events = events
		self._browser = browser

	def launch(self, headless: bool) -> FakeBrowser:
		self._events.append(f"chromium.launch:{headless}")
		return self._browser


class FakePlaywright:
	def __init__(self, events: list[str], chromium: FakeChromium, state: dict[str, bool]) -> None:
		self._events = events
		self.chromium = chromium
		self._state = state

	def __enter__(self):
		self._events.append("playwright.enter")
		return self

	def __exit__(self, exc_type, exc, tb):
		self._events.append("playwright.exit")
		self._state["stopped"] = True
		return False


def build_fake_playwright_module(events: list[str], *, page: FakePage, context_close_error: Exception | None = None, browser_close_error: Exception | None = None) -> tuple[types.ModuleType, dict[str, bool]]:
	playwright_state = {"stopped": False}
	context = FakeContext(events, page, playwright_state, close_error=context_close_error)
	browser = FakeBrowser(events, context, playwright_state, close_error=browser_close_error)
	chromium = FakeChromium(events, browser)
	playwright_module = types.ModuleType("playwright.sync_api")
	playwright_module.sync_playwright = lambda: FakePlaywright(events, chromium, playwright_state)
	playwright_module.Error = FakePlaywrightError
	playwright_module.TimeoutError = FakePlaywrightTimeoutError
	parent_module = types.ModuleType("playwright")
	parent_module.__path__ = []
	return playwright_module, playwright_state


def fake_playwright_module_with(page: FakePage, *, context_close_error: Exception | None = None, browser_close_error: Exception | None = None):
	events: list[str] = []
	playwright_state = {"stopped": False}
	page._events = events
	context = FakeContext(events, page, playwright_state, close_error=context_close_error)
	browser = FakeBrowser(events, context, playwright_state, close_error=browser_close_error)
	chromium = FakeChromium(events, browser)
	playwright_module = types.ModuleType("playwright.sync_api")
	playwright_module.sync_playwright = lambda: FakePlaywright(events, chromium, playwright_state)
	playwright_module.Error = FakePlaywrightError
	playwright_module.TimeoutError = FakePlaywrightTimeoutError
	parent_module = types.ModuleType("playwright")
	parent_module.__path__ = []
	return events, playwright_state, parent_module, playwright_module


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
	yield app
	db.session.remove()
	db.drop_all()
	db.engine.dispose()
	context.pop()


def build_allowed_robots_manager() -> Mock:
	robots_manager = Mock()
	parser = Mock()
	parser.can_fetch.return_value = True
	robots_manager.load_rules.return_value = parser
	robots_manager.get_crawl_delay.return_value = 0
	return robots_manager


def test_robots_denied_prevents_browser_launch() -> None:
	robots_manager = Mock()
	parser = Mock()
	parser.can_fetch.return_value = False
	robots_manager.load_rules.return_value = parser
	browser_fetcher = FakeFetcher(result=FetchResult(html=TRENDYOL_HTML, final_url=TRENDYOL_URL))
	scraper = TrendyolScraper(robots_manager=robots_manager, fetchers={"playwright": browser_fetcher})

	result = scraper.scrape_product(TRENDYOL_URL, fetch_strategy="playwright")

	assert result is None
	assert scraper.last_failure_reason == "robots_denied"
	assert browser_fetcher.calls == []


def test_hepsiburada_robots_denied_prevents_browser_launch() -> None:
	robots_manager = Mock()
	parser = Mock()
	parser.can_fetch.return_value = False
	robots_manager.load_rules.return_value = parser
	browser_fetcher = FakeFetcher(result=FetchResult(html=HEPSIBURADA_HTML, final_url=HEPSIBURADA_URL))
	scraper = HepsiburadaScraper(robots_manager=robots_manager, fetchers={"playwright": browser_fetcher})

	result = scraper.scrape_product(HEPSIBURADA_URL, fetch_strategy="playwright")

	assert result is None
	assert scraper.last_failure_reason == "robots_denied"
	assert browser_fetcher.calls == []


def test_supported_trendyol_url_selects_trendyol_parser() -> None:
	scraper = create_scraper_for_url(TRENDYOL_URL)

	assert isinstance(scraper, TrendyolScraper)


def test_supported_hepsiburada_url_selects_hepsiburada_parser() -> None:
	scraper = create_scraper_for_url(HEPSIBURADA_URL)

	assert isinstance(scraper, HepsiburadaScraper)


def test_browser_html_is_passed_to_existing_parser() -> None:
	robots_manager = build_allowed_robots_manager()
	browser_fetcher = FakeFetcher(result=FetchResult(html=TRENDYOL_HTML, final_url=TRENDYOL_URL, page_title="Casio Product"))
	scraper = TrendyolScraper(robots_manager=robots_manager, fetchers={"playwright": browser_fetcher})

	with patch.object(scraper, "parse_product_page", wraps=scraper.parse_product_page) as parse_mock:
		result = scraper.scrape_product(TRENDYOL_URL, fetch_strategy="playwright")

	assert result is not None
	parse_mock.assert_called_once_with(TRENDYOL_HTML, TRENDYOL_URL)


def test_browser_timeout_sets_structured_failure() -> None:
	robots_manager = build_allowed_robots_manager()
	browser_fetcher = FakeFetcher(error=FetcherError("browser_timeout", "The browser timed out before a stable product page was returned."))
	scraper = TrendyolScraper(robots_manager=robots_manager, fetchers={"playwright": browser_fetcher})

	result = scraper.scrape_product(TRENDYOL_URL, fetch_strategy="playwright")

	assert result is None
	assert scraper.last_failure_reason == "browser_timeout"


def test_challenge_page_sets_challenge_detected_failure() -> None:
	robots_manager = build_allowed_robots_manager()
	browser_fetcher = FakeFetcher(result=FetchResult(html=CHALLENGE_HTML, final_url=TRENDYOL_URL, page_title="Access Denied"))
	scraper = TrendyolScraper(robots_manager=robots_manager, fetchers={"playwright": browser_fetcher})

	result = scraper.scrape_product(TRENDYOL_URL, fetch_strategy="playwright")

	assert result is None
	assert scraper.last_failure_reason == "challenge_detected"


def test_browser_navigation_failure_sets_structured_failure() -> None:
	robots_manager = build_allowed_robots_manager()
	browser_fetcher = FakeFetcher(error=FetcherError("browser_navigation_failed", "The browser could not complete navigation to the requested product page."))
	scraper = HepsiburadaScraper(robots_manager=robots_manager, fetchers={"playwright": browser_fetcher})

	result = scraper.scrape_product(HEPSIBURADA_URL, fetch_strategy="playwright")

	assert result is None
	assert scraper.last_failure_reason == "browser_navigation_failed"


def test_successful_playwright_dto_uses_existing_persistence(app_context) -> None:
	robots_manager = build_allowed_robots_manager()
	browser_fetcher = FakeFetcher(result=FetchResult(html=TRENDYOL_HTML, final_url=TRENDYOL_URL, page_title="Casio Product"))
	scraper = TrendyolScraper(robots_manager=robots_manager, fetchers={"playwright": browser_fetcher})

	result = scrape_and_save_product(TRENDYOL_URL, scraper=scraper, fetch_strategy="playwright")

	assert result is not None
	assert Product.query.count() == 1
	assert Seller.query.count() == 1
	assert Listing.query.count() == 1
	assert PriceHistory.query.count() == 1
	assert Listing.query.one().external_product_id == "33139591"


def test_successful_hepsiburada_playwright_dto_uses_existing_sellerless_persistence(app_context) -> None:
	robots_manager = build_allowed_robots_manager()
	browser_fetcher = FakeFetcher(result=FetchResult(html=HEPSIBURADA_HTML, final_url=HEPSIBURADA_URL, page_title="Hepsiburada Product"))
	scraper = HepsiburadaScraper(robots_manager=robots_manager, fetchers={"playwright": browser_fetcher})

	result = scrape_and_save_product(HEPSIBURADA_URL, scraper=scraper, fetch_strategy="playwright")

	assert result is not None
	assert Product.query.count() == 1
	assert Seller.query.count() == 0
	assert Listing.query.count() == 1
	assert PriceHistory.query.count() == 1
	assert Listing.query.one().external_product_id == "SACSA159WAN1DF"
	assert Listing.query.one().current_price == Decimal("7499.90")
	assert Listing.query.one().seller_id is None


def test_second_hepsiburada_playwright_save_reuses_listing_and_increments_history(app_context) -> None:
	robots_manager = build_allowed_robots_manager()
	browser_fetcher = FakeFetcher(result=FetchResult(html=HEPSIBURADA_HTML, final_url=HEPSIBURADA_URL, page_title="Hepsiburada Product"))
	scraper = HepsiburadaScraper(robots_manager=robots_manager, fetchers={"playwright": browser_fetcher})

	first = scrape_and_save_product(HEPSIBURADA_URL, scraper=scraper, fetch_strategy="playwright")
	second = scrape_and_save_product(HEPSIBURADA_URL, scraper=scraper, fetch_strategy="playwright")

	assert first is not None
	assert second is not None
	assert Product.query.count() == 1
	assert Seller.query.count() == 0
	assert Listing.query.count() == 1
	assert PriceHistory.query.count() == 2
	assert first.listing.id == second.listing.id


def test_requests_fetch_strategy_still_works() -> None:
	robots_manager = build_allowed_robots_manager()
	requests_fetcher = FakeFetcher(result=FetchResult(html=TRENDYOL_HTML, final_url=TRENDYOL_URL, status_code=200))
	scraper = TrendyolScraper(robots_manager=robots_manager, fetchers={"requests": requests_fetcher})

	result = scrape_product(TRENDYOL_URL, scraper=scraper, fetch_strategy="requests")

	assert result is not None
	assert result.current_price == Decimal("7499.90")
	assert requests_fetcher.calls[0]["headless"] is True


def test_invalid_fetch_strategy_is_rejected() -> None:
	robots_manager = build_allowed_robots_manager()
	scraper = TrendyolScraper(robots_manager=robots_manager)

	result = scrape_product(TRENDYOL_URL, scraper=scraper, fetch_strategy="invalid")

	assert result is None
	assert scraper.last_failure_reason == "invalid_fetch_strategy"


def test_no_database_write_on_browser_failure(app_context) -> None:
	robots_manager = build_allowed_robots_manager()
	browser_fetcher = FakeFetcher(error=FetcherError("browser_timeout", "The browser timed out before a stable product page was returned."))
	scraper = TrendyolScraper(robots_manager=robots_manager, fetchers={"playwright": browser_fetcher})

	result = scrape_and_save_product(TRENDYOL_URL, scraper=scraper, fetch_strategy="playwright")

	assert result is None
	assert Product.query.count() == 0
	assert Seller.query.count() == 0
	assert Listing.query.count() == 0
	assert PriceHistory.query.count() == 0


def test_playwright_success_closes_resources_before_stop(monkeypatch) -> None:
	page = FakePage(
		[],
		html=TRENDYOL_HTML,
		final_url=TRENDYOL_URL,
		page_title="Casio Product",
		playwright_state={"stopped": False},
	)
	events, state, parent_module, sync_api_module = fake_playwright_module_with(page)
	page._playwright_state = state
	monkeypatch.setitem(sys.modules, "playwright", parent_module)
	monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api_module)

	result = _run_playwright_fetch(
		TRENDYOL_URL,
		user_agent="WatchPriceTracker/1.0",
		timeout_seconds=10,
		headless=True,
	)

	assert result.html == TRENDYOL_HTML
	assert events == [
		"playwright.enter",
		"chromium.launch:True",
		"browser.new_context:WatchPriceTracker/1.0",
		"context.new_page",
		f"goto:{TRENDYOL_URL}:domcontentloaded:10000",
		"content",
		"title",
		"page.close",
		"context.close",
		"browser.close",
		"playwright.exit",
	]
	assert state["stopped"] is True


def test_playwright_navigation_failure_preserves_original_error_when_cleanup_also_fails(monkeypatch) -> None:
	page = FakePage(
		[],
		html=TRENDYOL_HTML,
		final_url=TRENDYOL_URL,
		page_title="Casio Product",
		goto_error=FakePlaywrightError("navigation failed"),
		close_error=RuntimeError("cleanup failed"),
		playwright_state={"stopped": False},
	)
	events, state, parent_module, sync_api_module = fake_playwright_module_with(page, context_close_error=RuntimeError("context cleanup failed"))
	page._playwright_state = state
	monkeypatch.setitem(sys.modules, "playwright", parent_module)
	monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api_module)

	with pytest.raises(FetcherError) as error_info:
		_run_playwright_fetch(
			TRENDYOL_URL,
			user_agent="WatchPriceTracker/1.0",
			timeout_seconds=10,
			headless=True,
		)

	assert error_info.value.reason == "browser_navigation_failed"
	assert events == [
		"playwright.enter",
		"chromium.launch:True",
		"browser.new_context:WatchPriceTracker/1.0",
		"context.new_page",
		f"goto:{TRENDYOL_URL}:domcontentloaded:10000",
		"page.close",
		"context.close",
		"browser.close",
		"playwright.exit",
	]
	assert state["stopped"] is True


def test_playwright_timeout_still_cleans_resources(monkeypatch) -> None:
	page = FakePage(
		[],
		html=TRENDYOL_HTML,
		final_url=TRENDYOL_URL,
		page_title="Casio Product",
		goto_error=FakePlaywrightTimeoutError("timeout"),
		playwright_state={"stopped": False},
	)
	events, state, parent_module, sync_api_module = fake_playwright_module_with(page)
	page._playwright_state = state
	monkeypatch.setitem(sys.modules, "playwright", parent_module)
	monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api_module)

	with pytest.raises(FetcherError) as error_info:
		_run_playwright_fetch(
			TRENDYOL_URL,
			user_agent="WatchPriceTracker/1.0",
			timeout_seconds=10,
			headless=True,
		)

	assert error_info.value.reason == "browser_timeout"
	assert events == [
		"playwright.enter",
		"chromium.launch:True",
		"browser.new_context:WatchPriceTracker/1.0",
		"context.new_page",
		f"goto:{TRENDYOL_URL}:domcontentloaded:10000",
		"page.close",
		"context.close",
		"browser.close",
		"playwright.exit",
	]
	assert state["stopped"] is True