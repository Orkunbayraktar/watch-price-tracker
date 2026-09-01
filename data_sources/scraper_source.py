"""Adapters that expose the existing web scrapers as generic data sources."""

from __future__ import annotations

from pathlib import Path

from data_sources.base import BaseDataSource, DataSourceRecord, DataSourceResult
from services.scraping_service import create_scraper_for_url, scrape_product


class WebScraperDataSource(BaseDataSource):
	"""Use the existing scraper registry as a normalized data source."""

	def load(self, source: str | Path) -> DataSourceResult:
		url = str(source)
		scraper = create_scraper_for_url(url)
		if scraper is None:
			return DataSourceResult(
				source_failure_reason="unsupported_url",
				source_failure_details="The provided URL is not supported by the configured scraper adapters.",
			)

		scraped_data = scrape_product(url, scraper=scraper)
		if scraped_data is None:
			return DataSourceResult(
				source_failure_reason=scraper.last_failure_reason or "scrape_failed",
				source_failure_details=scraper.last_failure_details or "The scraper could not produce valid product data.",
			)

		return DataSourceResult(records=[DataSourceRecord(data=scraped_data)])