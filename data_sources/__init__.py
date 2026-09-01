"""Generic normalized product data sources."""

from data_sources.base import BaseDataSource, DataSourceRecord, DataSourceResult, DataSourceValidationError
from data_sources.file_source import FileDataSource
from data_sources.scraper_source import WebScraperDataSource


__all__ = [
	"BaseDataSource",
	"DataSourceRecord",
	"DataSourceResult",
	"DataSourceValidationError",
	"FileDataSource",
	"WebScraperDataSource",
]