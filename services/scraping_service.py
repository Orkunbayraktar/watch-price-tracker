"""Simple scraper orchestration helpers."""

from __future__ import annotations

import logging

from scrapers.models import ScrapedProductData
from scrapers.trendyol_scraper import TrendyolScraper


logger = logging.getLogger(__name__)


def scrape_product(url: str, scraper: TrendyolScraper | None = None) -> ScrapedProductData | None:
	"""Scrape a single supported product URL and return normalized data."""
	working_scraper = scraper or TrendyolScraper()
	if not working_scraper.is_supported_url(url):
		logger.warning("No scraper available for URL: %s", url)
		return None

	return working_scraper.scrape_product(url)
