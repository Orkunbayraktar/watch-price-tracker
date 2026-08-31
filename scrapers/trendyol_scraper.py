"""Minimal Trendyol scraper shell."""

from scrapers.base_scraper import BaseScraper


class TrendyolScraper(BaseScraper):
	"""Trendyol-specific scraper shell for future marketplace logic."""

	platform = "trendyol"
