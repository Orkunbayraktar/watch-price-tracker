"""Robots.txt loading and access control helpers.

The manager is intentionally fail-safe: if robots.txt cannot be fetched or
parsed with enough confidence, URL access is denied by default.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse, urlunparse
from urllib.robotparser import RobotFileParser

from config.settings import Config
import requests


logger = logging.getLogger(__name__)


class RobotsManager:
	"""Manage robots.txt rules for scraper URL access checks."""

	def __init__(
		self,
		user_agent: str | None = None,
		timeout: int | float | None = None,
		default_request_delay: int | float | None = None,
	) -> None:
		self.user_agent = user_agent or Config.SCRAPER_USER_AGENT
		self.timeout = timeout if timeout is not None else Config.ROBOTS_TIMEOUT
		self.default_request_delay = (
			default_request_delay
			if default_request_delay is not None
			else Config.DEFAULT_REQUEST_DELAY
		)
		self._cache: dict[str, RobotFileParser | None] = {}

	def get_robots_url(self, url: str) -> str:
		"""Build the robots.txt URL for an absolute URL."""
		parsed_url = urlparse(url)
		if not parsed_url.scheme or not parsed_url.netloc:
			raise ValueError(f"Invalid absolute URL for robots lookup: {url}")

		return urlunparse(
			(parsed_url.scheme, parsed_url.netloc, "/robots.txt", "", "", "")
		)

	def load_rules(self, url: str) -> RobotFileParser | None:
		"""Load and cache robots.txt rules for the URL domain.

		Returns None when rules cannot be loaded. Callers should treat that as a
		denial and avoid making outbound requests.
		"""
		try:
			robots_url = self.get_robots_url(url)
		except ValueError as error:
			logger.warning("Invalid URL for robots check: %s", error)
			return None

		if robots_url in self._cache:
			logger.info("Using cached robots.txt rules for %s", robots_url)
			return self._cache[robots_url]

		parser = RobotFileParser()
		parser.set_url(robots_url)

		try:
			response = requests.get(
				robots_url,
				headers={"User-Agent": self.user_agent},
				timeout=self.timeout,
			)
			response.raise_for_status()
			content = response.text
			if not content or not content.strip():
				raise ValueError("robots.txt response was empty")
			parser.parse(content.splitlines())
		except (requests.RequestException, OSError, ValueError) as error:
			logger.warning("Failed to load robots.txt from %s: %s", robots_url, error)
			self._cache[robots_url] = None
			return None

		logger.info("Loaded robots.txt rules from %s", robots_url)
		self._cache[robots_url] = parser
		return parser

	def can_fetch(self, url: str, user_agent: str | None = None) -> bool:
		"""Return whether the URL may be fetched for the given user agent.

		Fail-safe behavior: if robots.txt cannot be verified, this method returns
		False so the scraper does not continue blindly.
		"""
		parser = self.load_rules(url)
		agent = user_agent or self.user_agent

		if parser is None:
			logger.warning("Robots.txt verification failed for %s; denying access", url)
			return False

		is_allowed = parser.can_fetch(agent, url)
		if not is_allowed:
			logger.info("Robots.txt blocked %s for user-agent %s", url, agent)

		return is_allowed

	def get_crawl_delay(self, url: str, user_agent: str | None = None) -> int | float | None:
		"""Return crawl-delay from robots.txt or fall back to configured default."""
		parser = self.load_rules(url)
		agent = user_agent or self.user_agent

		if parser is None:
			return self.default_request_delay

		crawl_delay = parser.crawl_delay(agent)
		if crawl_delay is None:
			return self.default_request_delay

		return crawl_delay
