"""Playwright-based HTML fetcher for live browser feasibility checks."""

from __future__ import annotations

import logging
from typing import Callable

from scrapers.fetchers.base import FetchResult, FetcherError


logger = logging.getLogger(__name__)


class PlaywrightFetcher:
	"""Fetch HTML through a normal Chromium browser without stealth tooling."""

	def __init__(self, runner: Callable[..., FetchResult] | None = None) -> None:
		self._runner = runner or _run_playwright_fetch

	def fetch(
		self,
		url: str,
		*,
		user_agent: str,
		timeout_seconds: int | float,
		headless: bool = True,
	) -> FetchResult:
		return self._runner(
			url,
			user_agent=user_agent,
			timeout_seconds=timeout_seconds,
			headless=headless,
		)


def _run_playwright_fetch(
	url: str,
	*,
	user_agent: str,
	timeout_seconds: int | float,
	headless: bool,
) -> FetchResult:
	try:
		from playwright.sync_api import Error as PlaywrightError
		from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
		from playwright.sync_api import sync_playwright
	except ImportError as error:
		raise FetcherError(
			"browser_launch_failed",
			"Playwright is not installed. Install project dependencies and run 'python -m playwright install chromium'.",
		) from error

	browser = None
	context = None
	page = None
	stage = "launch"
	timeout_ms = int(float(timeout_seconds) * 1000)
	result: FetchResult | None = None

	try:
		with sync_playwright() as playwright:
			browser = playwright.chromium.launch(headless=headless)
			stage = "context"
			context = browser.new_context(user_agent=user_agent)
			page = context.new_page()
			try:
				stage = "navigate"
				response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
				stage = "content"
				html = page.content()
				page_title = page.title()
				final_url = page.url
				status_code = response.status if response is not None else None
				logger.info(
					"Playwright navigation completed for %s with status %s",
					url,
					status_code if status_code is not None else "unknown",
				)
				result = FetchResult(
					html=html,
					final_url=final_url,
					status_code=status_code,
					page_title=page_title,
				)
			finally:
				_close_playwright_resource("page", page, "close")
				_close_playwright_resource("context", context, "close")
				_close_playwright_resource("browser", browser, "close")
		if result is None:
			raise FetcherError(
				"browser_navigation_failed",
				"The browser could not complete navigation to the requested product page.",
			)
		return result
	except PlaywrightTimeoutError as error:
		logger.warning("Playwright navigation timed out for %s: %s", url, error)
		raise FetcherError(
			"browser_timeout",
			"The browser timed out before a stable product page was returned.",
		) from error
	except PlaywrightError as error:
		logger.warning("Playwright %s failed for %s: %s", stage, url, error)
		if stage in {"launch", "context"}:
			raise FetcherError(
				"browser_launch_failed",
				"Chromium could not be launched for the requested page.",
			) from error
		raise FetcherError(
			"browser_navigation_failed",
			"The browser could not complete navigation to the requested product page.",
		) from error
	except Exception as error:
		logger.warning("Unexpected Playwright failure for %s: %s", url, error)
		raise FetcherError(
			"browser_navigation_failed",
			"The browser could not complete navigation to the requested product page.",
		) from error


def _close_playwright_resource(label: str, resource: object | None, method_name: str) -> None:
	"""Close a Playwright resource without hiding the original fetch outcome."""
	if resource is None:
		return

	close_method = getattr(resource, method_name, None)
	if close_method is None:
		return

	try:
		close_method()
	except Exception as error:
		logger.warning("Playwright %s cleanup failed: %s", label, error)