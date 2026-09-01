"""Simple scraper orchestration helpers."""

from __future__ import annotations

import logging

from scrapers.hepsiburada_scraper import HepsiburadaScraper
from scrapers.models import ScrapedProductData
from scrapers.product_page_scraper import ProductPageScraper
from scrapers.trendyol_scraper import TrendyolScraper
from services.persistence_service import PersistenceError, PersistenceResult, save_scraped_product


logger = logging.getLogger(__name__)


SCRAPER_CLASSES: tuple[type[ProductPageScraper], ...] = (
	TrendyolScraper,
	HepsiburadaScraper,
)


def create_scraper_for_url(url: str) -> ProductPageScraper | None:
	"""Create the first scraper that explicitly supports the URL."""
	for scraper_class in SCRAPER_CLASSES:
		if scraper_class.is_supported_url(url):
			return scraper_class()

	return None


def scrape_product(url: str, scraper: ProductPageScraper | None = None) -> ScrapedProductData | None:
	"""Scrape a single supported product URL and return normalized data."""
	working_scraper = scraper or create_scraper_for_url(url)
	if working_scraper is None:
		logger.warning("No scraper available for URL: %s", url)
		return None

	if not working_scraper.is_supported_url(url):
		logger.warning("No scraper available for URL: %s", url)
		return None

	return working_scraper.scrape_product(url)


def scrape_and_save_product(
	url: str,
	scraper: ProductPageScraper | None = None,
) -> PersistenceResult | None:
	"""Scrape a single supported product URL and persist the normalized result."""
	scraped_data = scrape_product(url, scraper=scraper)
	if scraped_data is None:
		return None

	try:
		return save_scraped_product(scraped_data)
	except PersistenceError:
		logger.exception("Scrape succeeded but persistence failed for %s", url)
		return None
