"""Common scraper behavior shared by marketplace-specific scrapers."""

from config.settings import Config
from scrapers.robots_manager import RobotsManager


class BaseScraper:
	"""Base scraper that enforces robots.txt checks before future requests."""

	platform = "unknown"

	def __init__(
		self,
		robots_manager: RobotsManager | None = None,
		user_agent: str | None = None,
	) -> None:
		self.user_agent = user_agent or Config.SCRAPER_USER_AGENT
		self.robots_manager = robots_manager or RobotsManager(user_agent=self.user_agent)

	def is_allowed(self, url: str) -> bool:
		"""Check whether the URL is allowed by robots.txt."""
		return self.robots_manager.can_fetch(url, user_agent=self.user_agent)

	def get_crawl_delay(self, url: str) -> int | float | None:
		"""Return the robots crawl-delay or the configured default delay."""
		return self.robots_manager.get_crawl_delay(url, user_agent=self.user_agent)

	def ensure_allowed(self, url: str) -> None:
		"""Raise an error if robots.txt does not allow access to the URL."""
		if not self.is_allowed(url):
			raise PermissionError(f"Robots.txt does not allow scraping this URL: {url}")
