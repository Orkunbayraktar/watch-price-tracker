"""Requests-based HTML fetcher used by the existing scraper behavior."""

from __future__ import annotations

import logging

import requests
from requests import Session

from scrapers.fetchers.base import FetchResult, FetcherError


logger = logging.getLogger(__name__)


class RequestsFetcher:
	"""Fetch HTML with requests while preserving the existing failure model."""

	def __init__(self, session: Session | None = None) -> None:
		self.session = session or requests.Session()

	def fetch(
		self,
		url: str,
		*,
		user_agent: str,
		timeout_seconds: int | float,
		headless: bool = True,
	) -> FetchResult:
		del headless
		try:
			response = self.session.get(
				url,
				headers={"User-Agent": user_agent},
				timeout=timeout_seconds,
			)
			response.raise_for_status()
		except requests.Timeout as error:
			logger.warning("Requests fetch timed out for %s: %s", url, error)
			raise FetcherError("timeout", "The request timed out before a response was received.") from error
		except requests.HTTPError as error:
			status_code = error.response.status_code if error.response is not None else None
			logger.warning("Requests fetch returned HTTP error for %s: %s", url, status_code)
			if status_code == 403:
				raise FetcherError("http_forbidden", "The server returned HTTP 403 and blocked the request.") from error
			if status_code == 429:
				raise FetcherError("rate_limited", "The server returned HTTP 429 due to rate limiting.") from error
			raise FetcherError(
				"http_error",
				f"The server returned an HTTP error: {status_code or 'unknown'}.",
			) from error
		except requests.RequestException as error:
			logger.warning("Requests fetch failed for %s: %s", url, error)
			raise FetcherError(
				"request_failed",
				"The HTTP request failed before valid HTML could be retrieved.",
			) from error

		return FetchResult(
			html=response.text,
			final_url=response.url,
			status_code=response.status_code,
			page_title=None,
		)