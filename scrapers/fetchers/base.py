"""Base fetcher contracts for HTML acquisition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class FetchResult:
	"""Raw HTML returned by one fetch strategy."""

	html: str
	final_url: str
	status_code: int | None = None
	page_title: str | None = None


class FetcherError(Exception):
	"""Structured fetch error that maps directly into scraper failure states."""

	def __init__(self, reason: str, details: str) -> None:
		super().__init__(details)
		self.reason = reason
		self.details = details


class BaseFetcher(Protocol):
	"""Protocol implemented by concrete HTML acquisition strategies."""

	def fetch(
		self,
		url: str,
		*,
		user_agent: str,
		timeout_seconds: int | float,
		headless: bool = True,
	) -> FetchResult:
		"""Return raw HTML for a product page or raise a structured fetch error."""