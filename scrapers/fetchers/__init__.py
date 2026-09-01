"""Fetcher abstractions used by the scraper layer."""

from scrapers.fetchers.base import BaseFetcher, FetchResult, FetcherError
from scrapers.fetchers.playwright_fetcher import PlaywrightFetcher
from scrapers.fetchers.requests_fetcher import RequestsFetcher


__all__ = [
	"BaseFetcher",
	"FetchResult",
	"FetcherError",
	"PlaywrightFetcher",
	"RequestsFetcher",
]