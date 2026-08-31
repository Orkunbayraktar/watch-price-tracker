"""Tests for the robots.txt control layer."""

from unittest.mock import Mock, patch
import unittest
from urllib.error import URLError

from scrapers.base_scraper import BaseScraper
from scrapers.robots_manager import RobotsManager


class FakeHTTPResponse:
	"""Simple context-manager response for mocked urlopen calls."""

	def __init__(self, payload: str) -> None:
		self.payload = payload.encode("utf-8")

	def read(self) -> bytes:
		return self.payload

	def __enter__(self) -> "FakeHTTPResponse":
		return self

	def __exit__(self, exc_type, exc_val, exc_tb) -> None:
		return None


class RobotsManagerTests(unittest.TestCase):
	"""Verify robots.txt URL handling, caching, and fail-safe checks."""

	def test_get_robots_url_builds_expected_address(self) -> None:
		manager = RobotsManager()

		robots_url = manager.get_robots_url("https://shop.example.com/product/123?ref=1")

		self.assertEqual(robots_url, "https://shop.example.com/robots.txt")

	@patch("scrapers.robots_manager.urlopen")
	def test_can_fetch_returns_true_for_allowed_url(self, mock_urlopen: Mock) -> None:
		mock_urlopen.return_value = FakeHTTPResponse("User-agent: *\nDisallow: /private")
		manager = RobotsManager()

		is_allowed = manager.can_fetch("https://example.com/products/watch-1")

		self.assertTrue(is_allowed)

	@patch("scrapers.robots_manager.urlopen")
	def test_can_fetch_returns_false_for_disallowed_url(self, mock_urlopen: Mock) -> None:
		mock_urlopen.return_value = FakeHTTPResponse("User-agent: *\nDisallow: /private")
		manager = RobotsManager()

		is_allowed = manager.can_fetch("https://example.com/private/watch-1")

		self.assertFalse(is_allowed)

	@patch("scrapers.robots_manager.urlopen")
	def test_load_rules_uses_cache_for_same_domain(self, mock_urlopen: Mock) -> None:
		mock_urlopen.return_value = FakeHTTPResponse("User-agent: *\nDisallow:")
		manager = RobotsManager()

		first_check = manager.can_fetch("https://example.com/products/watch-1")
		second_check = manager.can_fetch("https://example.com/products/watch-2")

		self.assertTrue(first_check)
		self.assertTrue(second_check)
		self.assertEqual(mock_urlopen.call_count, 1)

	@patch("scrapers.robots_manager.urlopen")
	def test_can_fetch_is_fail_safe_when_robots_cannot_be_loaded(self, mock_urlopen: Mock) -> None:
		mock_urlopen.side_effect = URLError("network unavailable")
		manager = RobotsManager()

		is_allowed = manager.can_fetch("https://example.com/products/watch-1")

		self.assertFalse(is_allowed)

	def test_custom_user_agent_is_passed_to_robot_parser(self) -> None:
		manager = RobotsManager()
		robots_url = manager.get_robots_url("https://example.com/products/watch-1")
		parser = Mock()
		parser.can_fetch.return_value = True
		manager._cache[robots_url] = parser

		is_allowed = manager.can_fetch(
			"https://example.com/products/watch-1",
			user_agent="WatchPriceTracker-Test/2.0",
		)

		self.assertTrue(is_allowed)
		parser.can_fetch.assert_called_once_with(
			"WatchPriceTracker-Test/2.0",
			"https://example.com/products/watch-1",
		)

	def test_base_scraper_uses_robots_manager_for_access_control(self) -> None:
		robots_manager = Mock()
		robots_manager.can_fetch.return_value = False
		scraper = BaseScraper(robots_manager=robots_manager, user_agent="WatchPriceTracker/1.0")

		is_allowed = scraper.is_allowed("https://example.com/private/watch-1")

		self.assertFalse(is_allowed)
		robots_manager.can_fetch.assert_called_once_with(
			"https://example.com/private/watch-1",
			user_agent="WatchPriceTracker/1.0",
		)

		with self.assertRaises(PermissionError):
			scraper.ensure_allowed("https://example.com/private/watch-1")


if __name__ == "__main__":
	unittest.main()