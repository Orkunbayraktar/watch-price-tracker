"""Tests for sequential batch scraping and ScrapeRun tracking."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from database.db import db, initialize_database
from database.models import Listing, PriceHistory, Product, ScrapeRun, ScrapeRunItem, Seller
from scrapers.models import ScrapedProductData
from services.batch_scraping_service import (
	DUPLICATE_URL_REASON,
	ITEM_STATUS_FAILED,
	ITEM_STATUS_SKIPPED,
	ITEM_STATUS_SUCCESS,
	RUN_STATUS_COMPLETED,
	RUN_STATUS_COMPLETED_WITH_ERRORS,
	RUN_STATUS_FAILED,
	load_batch_urls,
	run_batch,
)


TRENDYOL_URL_ONE = "https://www.trendyol.com/casio/f-91w-p-1001"
TRENDYOL_URL_TWO = "https://www.trendyol.com/casio/a168-p-1002"
TRENDYOL_URL_THREE = "https://www.trendyol.com/casio/ga-2100-p-1003"
HEPSIBURADA_URL = "https://www.hepsiburada.com/casio-retro-pm-sacsa159wan1df"
UNSUPPORTED_URL = "https://example.com/not-supported"


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


class ScriptedTrendyolScraper:
	platform = "trendyol"
	responses: dict[str, ScrapedProductData] = {}
	failures: dict[str, tuple[str, str]] = {}
	call_urls: list[str] = []
	instance_count = 0

	def __init__(self, debug_parser: bool = False) -> None:
		type(self).instance_count += 1
		self.debug_parser = debug_parser
		self.last_failure_reason: str | None = None
		self.last_failure_details: str | None = None

	@classmethod
	def reset(cls) -> None:
		cls.responses = {}
		cls.failures = {}
		cls.call_urls = []
		cls.instance_count = 0

	@classmethod
	def is_supported_url(cls, url: str) -> bool:
		return url.startswith("https://www.trendyol.com/")

	def scrape_product(self, url: str, *, fetch_strategy: str = "requests", headed: bool = False) -> ScrapedProductData | None:
		type(self).call_urls.append(url)
		if url in self.failures:
			self.last_failure_reason, self.last_failure_details = self.failures[url]
			return None
		self.last_failure_reason = None
		self.last_failure_details = None
		return self.responses.get(url)


class ScriptedHepsiburadaScraper:
	platform = "hepsiburada"
	responses: dict[str, ScrapedProductData] = {}
	failures: dict[str, tuple[str, str]] = {}
	call_urls: list[str] = []
	instance_count = 0

	def __init__(self, debug_parser: bool = False) -> None:
		type(self).instance_count += 1
		self.debug_parser = debug_parser
		self.last_failure_reason: str | None = None
		self.last_failure_details: str | None = None

	@classmethod
	def reset(cls) -> None:
		cls.responses = {}
		cls.failures = {}
		cls.call_urls = []
		cls.instance_count = 0

	@classmethod
	def is_supported_url(cls, url: str) -> bool:
		return url.startswith("https://www.hepsiburada.com/")

	def scrape_product(self, url: str, *, fetch_strategy: str = "requests", headed: bool = False) -> ScrapedProductData | None:
		type(self).call_urls.append(url)
		if url in self.failures:
			self.last_failure_reason, self.last_failure_details = self.failures[url]
			return None
		self.last_failure_reason = None
		self.last_failure_details = None
		return self.responses.get(url)


def setup_scripted_scrapers() -> tuple[type[ScriptedTrendyolScraper], type[ScriptedHepsiburadaScraper]]:
	ScriptedTrendyolScraper.reset()
	ScriptedHepsiburadaScraper.reset()
	return (ScriptedTrendyolScraper, ScriptedHepsiburadaScraper)


def make_scraped_data(
	url: str,
	*,
	platform: str,
	name: str,
	external_product_id: str,
	price: str,
	seller_name: str | None = "Market Time",
) -> ScrapedProductData:
	return ScrapedProductData(
		platform=platform,
		product_name=name,
		brand="Casio",
		model=None,
		current_price=Decimal(price),
		old_price=None,
		discount_percentage=None,
		currency="TRY",
		seller_name=seller_name,
		seller_rating=None,
		availability="in_stock",
		product_url=url,
		external_product_id=external_product_id,
		scraped_at=datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
		visible_sales_count=None,
	)


def test_batch_accepts_multiple_urls_and_persists_successes(app_context) -> None:
	scraper_classes = setup_scripted_scrapers()
	ScriptedTrendyolScraper.responses = {
		TRENDYOL_URL_ONE: make_scraped_data(TRENDYOL_URL_ONE, platform="trendyol", name="Casio F-91W", external_product_id="1001", price="749.00"),
		TRENDYOL_URL_TWO: make_scraped_data(TRENDYOL_URL_TWO, platform="trendyol", name="Casio A168", external_product_id="1002", price="1499.00"),
	}

	result = run_batch([TRENDYOL_URL_ONE, TRENDYOL_URL_TWO], platform="trendyol", scraper_classes=scraper_classes)

	assert result.total == 2
	assert result.successful == 2
	assert result.failed == 0
	assert result.skipped == 0
	assert result.status == RUN_STATUS_COMPLETED
	assert Product.query.count() == 2
	assert Listing.query.count() == 2
	assert PriceHistory.query.count() == 2
	assert ScrapeRun.query.count() == 1
	assert ScrapeRunItem.query.count() == 2


def test_duplicate_url_processed_once_and_duplicate_item_is_skipped(app_context) -> None:
	scraper_classes = setup_scripted_scrapers()
	ScriptedTrendyolScraper.responses = {
		TRENDYOL_URL_ONE: make_scraped_data(TRENDYOL_URL_ONE, platform="trendyol", name="Casio F-91W", external_product_id="1001", price="749.00"),
	}

	result = run_batch([f"  {TRENDYOL_URL_ONE}#reviews  ", TRENDYOL_URL_ONE], platform="trendyol", scraper_classes=scraper_classes)

	assert result.total == 2
	assert result.successful == 1
	assert result.failed == 0
	assert result.skipped == 1
	assert result.item_results[1].status == ITEM_STATUS_SKIPPED
	assert result.item_results[1].failure_reason == DUPLICATE_URL_REASON
	assert ScriptedTrendyolScraper.call_urls == [TRENDYOL_URL_ONE]
	assert ScriptedTrendyolScraper.instance_count == 1
	assert Product.query.count() == 1
	assert Listing.query.count() == 1
	assert PriceHistory.query.count() == 1


def test_failed_result_does_not_stop_next_item(app_context) -> None:
	scraper_classes = setup_scripted_scrapers()
	ScriptedTrendyolScraper.failures = {
		TRENDYOL_URL_ONE: ("blocked_by_platform", "Platform temporarily blocked access."),
	}
	ScriptedTrendyolScraper.responses = {
		TRENDYOL_URL_TWO: make_scraped_data(TRENDYOL_URL_TWO, platform="trendyol", name="Casio A168", external_product_id="1002", price="1499.00"),
	}

	result = run_batch([TRENDYOL_URL_ONE, TRENDYOL_URL_TWO], platform="trendyol", scraper_classes=scraper_classes)

	assert result.successful == 1
	assert result.failed == 1
	assert result.status == RUN_STATUS_COMPLETED_WITH_ERRORS
	assert result.item_results[0].status == ITEM_STATUS_FAILED
	assert result.item_results[0].failure_reason == "blocked_by_platform"
	assert result.item_results[1].status == ITEM_STATUS_SUCCESS
	assert Product.query.count() == 1
	assert Listing.query.count() == 1
	assert PriceHistory.query.count() == 1


def test_all_failure_run_sets_failed_status(app_context) -> None:
	scraper_classes = setup_scripted_scrapers()
	ScriptedTrendyolScraper.failures = {
		TRENDYOL_URL_ONE: ("robots_denied", "robots.txt denied access."),
		TRENDYOL_URL_TWO: ("timeout", "Timed out."),
	}

	result = run_batch([TRENDYOL_URL_ONE, TRENDYOL_URL_TWO], platform="trendyol", scraper_classes=scraper_classes)

	assert result.successful == 0
	assert result.failed == 2
	assert result.status == RUN_STATUS_FAILED
	assert ScrapeRun.query.one().errors_count == 2
	assert Product.query.count() == 0
	assert Listing.query.count() == 0


def test_batch_creates_item_level_results_and_stores_failure_reason(app_context) -> None:
	scraper_classes = setup_scripted_scrapers()
	ScriptedTrendyolScraper.responses = {
		TRENDYOL_URL_ONE: make_scraped_data(TRENDYOL_URL_ONE, platform="trendyol", name="Casio F-91W", external_product_id="1001", price="749.00"),
	}
	ScriptedHepsiburadaScraper.failures = {
		HEPSIBURADA_URL: ("http_forbidden", "HTTP 403 response received."),
	}

	result = run_batch([TRENDYOL_URL_ONE, HEPSIBURADA_URL], scraper_classes=scraper_classes)
	run = ScrapeRun.query.one()
	items = ScrapeRunItem.query.order_by(ScrapeRunItem.id).all()

	assert result.run_id == run.id
	assert run.status == RUN_STATUS_COMPLETED_WITH_ERRORS
	assert len(items) == 2
	assert items[0].status == ITEM_STATUS_SUCCESS
	assert items[1].status == ITEM_STATUS_FAILED
	assert items[1].failure_reason == "http_forbidden"
	assert items[1].platform == "hepsiburada"


def test_success_result_updates_scrape_run_summary(app_context) -> None:
	scraper_classes = setup_scripted_scrapers()
	ScriptedTrendyolScraper.responses = {
		TRENDYOL_URL_ONE: make_scraped_data(TRENDYOL_URL_ONE, platform="trendyol", name="Casio F-91W", external_product_id="1001", price="749.00"),
	}

	run_batch([TRENDYOL_URL_ONE], platform="trendyol", scraper_classes=scraper_classes)
	run = ScrapeRun.query.one()

	assert run.platform == "trendyol"
	assert run.products_found == 1
	assert run.listings_found == 1
	assert run.errors_count == 0
	assert run.status == RUN_STATUS_COMPLETED
	assert run.finished_at is not None


def test_duplicate_product_and_listing_are_not_created_twice_when_url_reappears_in_later_run(app_context) -> None:
	scraper_classes = setup_scripted_scrapers()
	data = make_scraped_data(TRENDYOL_URL_ONE, platform="trendyol", name="Casio F-91W", external_product_id="1001", price="749.00")
	ScriptedTrendyolScraper.responses = {TRENDYOL_URL_ONE: data}

	first = run_batch([TRENDYOL_URL_ONE], platform="trendyol", scraper_classes=scraper_classes)
	second = run_batch([TRENDYOL_URL_ONE], platform="trendyol", scraper_classes=scraper_classes)

	assert first.successful == 1
	assert second.successful == 1
	assert Product.query.count() == 1
	assert Listing.query.count() == 1
	assert Seller.query.count() == 1
	assert PriceHistory.query.count() == 2


def test_unsupported_url_is_recorded_without_breaking_batch(app_context) -> None:
	scraper_classes = setup_scripted_scrapers()
	ScriptedTrendyolScraper.responses = {
		TRENDYOL_URL_ONE: make_scraped_data(TRENDYOL_URL_ONE, platform="trendyol", name="Casio F-91W", external_product_id="1001", price="749.00"),
	}

	result = run_batch([UNSUPPORTED_URL, TRENDYOL_URL_ONE], scraper_classes=scraper_classes)

	assert result.total == 2
	assert result.successful == 1
	assert result.failed == 1
	assert result.status == RUN_STATUS_COMPLETED_WITH_ERRORS
	assert result.item_results[0].failure_reason == "unsupported_url"


def test_empty_url_list_creates_failed_run_with_no_items(app_context) -> None:
	scraper_classes = setup_scripted_scrapers()

	result = run_batch([], platform="trendyol", scraper_classes=scraper_classes)

	assert result.total == 0
	assert result.successful == 0
	assert result.failed == 0
	assert result.skipped == 0
	assert result.status == RUN_STATUS_FAILED
	assert ScrapeRun.query.one().error_message == "No URLs were provided for the batch run."
	assert ScrapeRunItem.query.count() == 0


def test_load_batch_urls_ignores_blank_lines_and_comments(tmp_path: Path) -> None:
	url_file = tmp_path / "urls.txt"
	url_file.write_text(
		"\n# Trendyol watches\n  https://www.trendyol.com/casio/f-91w-p-1001\n\n   # another comment\nhttps://www.trendyol.com/casio/a168-p-1002\n",
		encoding="utf-8",
	)

	loaded_urls = load_batch_urls(url_file)

	assert loaded_urls == [TRENDYOL_URL_ONE, TRENDYOL_URL_TWO]