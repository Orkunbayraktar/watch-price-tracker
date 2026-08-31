"""Common scraper behavior shared by marketplace-specific scrapers."""

from __future__ import annotations

import logging
import time

from config.settings import Config
from scrapers.robots_manager import RobotsManager


logger = logging.getLogger(__name__)


class BaseScraper:
	"""Base scraper that enforces robots.txt checks before future requests."""

	platform = "unknown"

	def __init__(
		self,
		robots_manager: RobotsManager | None = None,
		user_agent: str | None = None,
		default_request_delay: int | float | None = None,
	) -> None:
		self.user_agent = user_agent or Config.SCRAPER_USER_AGENT
		self.robots_manager = robots_manager or RobotsManager(user_agent=self.user_agent)
		self.default_request_delay = (
			default_request_delay
			if default_request_delay is not None
			else Config.DEFAULT_REQUEST_DELAY
		)
		self._last_request_started_at: float | None = None

	def is_allowed(self, url: str) -> bool:
		"""Check whether the URL is allowed by robots.txt."""
		return self.robots_manager.can_fetch(url, user_agent=self.user_agent)

	def get_crawl_delay(self, url: str) -> int | float | None:
		"""Return the robots crawl-delay or the configured default delay."""
		return self.robots_manager.get_crawl_delay(url, user_agent=self.user_agent)

	def get_effective_delay(self, url: str) -> int | float:
		"""Return the effective delay between requests for the URL domain."""
		crawl_delay = self.get_crawl_delay(url) or 0
		return max(self.default_request_delay, crawl_delay)

	def wait_for_request_slot(self, url: str) -> None:
		"""Wait until the configured request delay has elapsed.

		The first request is not delayed. Later requests on the same scraper
		instance wait long enough to respect the effective crawl delay.
		"""
		if self._last_request_started_at is None:
			return

		effective_delay = self.get_effective_delay(url)
		elapsed = time.monotonic() - self._last_request_started_at
		remaining = effective_delay - elapsed
		if remaining > 0:
			logger.info("Waiting %.2f seconds before the next request to %s", remaining, self.platform)
			time.sleep(remaining)

	def mark_request_started(self) -> None:
		"""Record when the latest outbound request started."""
		self._last_request_started_at = time.monotonic()

	def ensure_allowed(self, url: str) -> None:
		"""Raise an error if robots.txt does not allow access to the URL."""
		if not self.is_allowed(url):
			raise PermissionError(f"Robots.txt does not allow scraping this URL: {url}")
