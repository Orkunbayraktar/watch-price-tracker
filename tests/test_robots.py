"""Tests for the robots.txt control layer."""

from unittest.mock import Mock, patch
import unittest

import requests

from scrapers.base_scraper import BaseScraper
from scrapers.hepsiburada_scraper import HepsiburadaScraper
from scrapers.robots_manager import RobotsManager


class FakeResponse:
	"""Simple response object for mocked requests.get calls."""

	def __init__(self, text: str, status_error: Exception | None = None) -> None:
		self.text = text
		self._status_error = status_error

	def raise_for_status(self) -> None:
		if self._status_error is not None:
			raise self._status_error


class RobotsManagerTests(unittest.TestCase):
	"""Verify robots.txt URL handling, caching, and fail-safe checks."""

	def test_get_robots_url_builds_expected_address(self) -> None:
		manager = RobotsManager()

		robots_url = manager.get_robots_url("https://shop.example.com/product/123?ref=1")

		self.assertEqual(robots_url, "https://shop.example.com/robots.txt")

	@patch("scrapers.robots_manager.requests.get")
	def test_robots_txt_is_fetched_and_allow_rule_returns_true(self, mock_get: Mock) -> None:
		mock_get.return_value = FakeResponse("User-agent: *\nDisallow: /private")
		manager = RobotsManager()

		is_allowed = manager.can_fetch("https://example.com/products/watch-1")

		self.assertTrue(is_allowed)
		mock_get.assert_called_once_with(
			"https://example.com/robots.txt",
			headers={"User-Agent": "WatchPriceTracker/1.0"},
			timeout=5,
		)

	@patch("scrapers.robots_manager.requests.get")
	def test_downloaded_robots_text_is_parsed_and_disallow_rule_returns_false(self, mock_get: Mock) -> None:
		mock_get.return_value = FakeResponse("User-agent: *\nDisallow: /private")
		manager = RobotsManager()

		is_allowed = manager.can_fetch("https://example.com/private/watch-1")

		self.assertFalse(is_allowed)

	@patch("scrapers.robots_manager.requests.get")
	def test_load_rules_uses_cache_for_same_domain(self, mock_get: Mock) -> None:
		mock_get.return_value = FakeResponse("User-agent: *\nDisallow:")
		manager = RobotsManager()

		first_check = manager.can_fetch("https://example.com/products/watch-1")
		second_check = manager.can_fetch("https://example.com/products/watch-2")

		self.assertTrue(first_check)
		self.assertTrue(second_check)
		self.assertEqual(mock_get.call_count, 1)

	@patch("scrapers.robots_manager.requests.get")
	def test_robots_http_403_is_fail_safe(self, mock_get: Mock) -> None:
		http_error = requests.HTTPError("403 Client Error")
		http_error.response = Mock(status_code=403)
		mock_get.return_value = FakeResponse("forbidden", http_error)
		manager = RobotsManager()

		is_allowed = manager.can_fetch("https://example.com/products/watch-1")

		self.assertFalse(is_allowed)

	@patch("scrapers.robots_manager.requests.get")
	def test_robots_http_429_is_fail_safe(self, mock_get: Mock) -> None:
		http_error = requests.HTTPError("429 Client Error")
		http_error.response = Mock(status_code=429)
		mock_get.return_value = FakeResponse("rate limited", http_error)
		manager = RobotsManager()

		is_allowed = manager.can_fetch("https://example.com/products/watch-1")

		self.assertFalse(is_allowed)

	@patch("scrapers.robots_manager.requests.get")
	def test_timeout_is_fail_safe(self, mock_get: Mock) -> None:
		mock_get.side_effect = requests.Timeout("timed out")
		manager = RobotsManager()

		is_allowed = manager.can_fetch("https://example.com/products/watch-1")

		self.assertFalse(is_allowed)

	@patch("scrapers.robots_manager.requests.get")
	def test_network_error_is_fail_safe(self, mock_get: Mock) -> None:
		mock_get.side_effect = requests.RequestException("network unavailable")
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

	@patch("scrapers.robots_manager.requests.get")
	def test_custom_configured_user_agent_is_used_for_robots_request(self, mock_get: Mock) -> None:
		mock_get.return_value = FakeResponse("User-agent: *\nDisallow:")
		manager = RobotsManager(user_agent="WatchPriceTracker-Test/2.0", timeout=9)

		manager.load_rules("https://example.com/products/watch-1")

		mock_get.assert_called_once_with(
			"https://example.com/robots.txt",
			headers={"User-Agent": "WatchPriceTracker-Test/2.0"},
			timeout=9,
		)

	@patch("scrapers.robots_manager.requests.get")
	def test_product_request_is_never_sent_if_robots_loading_fails(self, mock_get: Mock) -> None:
		http_error = requests.HTTPError("403 Client Error")
		http_error.response = Mock(status_code=403)
		mock_get.return_value = FakeResponse("forbidden", http_error)
		product_session = Mock()
		scraper = HepsiburadaScraper(session=product_session)

		result = scraper.scrape_product("https://www.hepsiburada.com/casio-retro-kol-saati-a159wa-n1df-pm-sacsa159wan1df")

		self.assertIsNone(result)
		self.assertEqual(scraper.last_failure_reason, "robots_load_failed")
		product_session.get.assert_not_called()

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