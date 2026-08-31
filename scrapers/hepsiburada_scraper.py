"""Minimal Hepsiburada scraper shell."""

from scrapers.base_scraper import BaseScraper


class HepsiburadaScraper(BaseScraper):
	"""Hepsiburada-specific scraper shell for future marketplace logic."""

	platform = "hepsiburada"
