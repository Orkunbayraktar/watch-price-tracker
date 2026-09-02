"""Simple scraper orchestration helpers."""

from __future__ import annotations

import logging

from scrapers.hepsiburada_scraper import HepsiburadaScraper
from scrapers.models import ScrapedProductData
from scrapers.product_page_scraper import ProductPageScraper
from scrapers.saatvesaat_scraper import SaatVeSaatScraper
from scrapers.trendyol_scraper import TrendyolScraper
from services.persistence_service import PersistenceError, PersistenceResult, save_scraped_product


logger = logging.getLogger(__name__)


SCRAPER_CLASSES: tuple[type[ProductPageScraper], ...] = (
	TrendyolScraper,
	HepsiburadaScraper,
	SaatVeSaatScraper,
)


def get_scraper_class_for_url(url: str) -> type[ProductPageScraper] | None:
	"""Return the registered scraper class that supports the provided URL."""
	for scraper_class in SCRAPER_CLASSES:
		if scraper_class.is_supported_url(url):
			return scraper_class
	return None


def create_scraper_for_url(url: str) -> ProductPageScraper | None:
	"""Create the first scraper that explicitly supports the URL."""
	scraper_class = get_scraper_class_for_url(url)
	if scraper_class is not None:
		return scraper_class()
	return None


def get_platform_for_url(url: str) -> str | None:
	"""Return the platform name for a supported product URL."""
	scraper_class = get_scraper_class_for_url(url)
	if scraper_class is None:
		return None
	return getattr(scraper_class, "platform", None)


def extract_external_product_id_for_url(url: str) -> str | None:
	"""Return the platform-specific product ID for a supported URL when available."""
	scraper_class = get_scraper_class_for_url(url)
	if scraper_class is None:
		return None
	return scraper_class.extract_external_product_id(url)


def scrape_product_with_strategy(
	url: str,
	*,
	scraper: ProductPageScraper | None = None,
	fetch_strategy: str = "requests",
	headed: bool = False,
) -> ScrapedProductData | None:
	"""Scrape a single supported product URL with an explicit fetch strategy."""
	working_scraper = scraper or create_scraper_for_url(url)
	if working_scraper is None:
		logger.warning("No scraper available for URL: %s", url)
		return None

	if not working_scraper.is_supported_url(url):
		logger.warning("No scraper available for URL: %s", url)
		return None

	if fetch_strategy == "requests" and headed is False:
		return working_scraper.scrape_product(url)
	return working_scraper.scrape_product(url, fetch_strategy=fetch_strategy, headed=headed)


def scrape_product(
	url: str,
	scraper: ProductPageScraper | None = None,
	*,
	fetch_strategy: str = "requests",
	headed: bool = False,
) -> ScrapedProductData | None:
	"""Scrape a single supported product URL and return normalized data."""
	return scrape_product_with_strategy(
		url,
		scraper=scraper,
		fetch_strategy=fetch_strategy,
		headed=headed,
	)


def scrape_and_save_product(
	url: str,
	scraper: ProductPageScraper | None = None,
	*,
	fetch_strategy: str = "requests",
	headed: bool = False,
) -> PersistenceResult | None:
	"""Scrape a single supported product URL and persist the normalized result."""
	scraped_data = scrape_product(url, scraper=scraper, fetch_strategy=fetch_strategy, headed=headed)
	if scraped_data is None:
		return None

	try:
		return save_scraped_product(scraped_data)
	except PersistenceError:
		logger.exception("Scrape succeeded but persistence failed for %s", url)
		return None
