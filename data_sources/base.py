"""Base abstractions for normalized watch product data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from scrapers.models import ScrapedProductData


@dataclass(slots=True)
class DataSourceRecord:
	"""A successfully normalized record returned by a data source."""

	data: ScrapedProductData
	row_number: int | None = None


@dataclass(slots=True)
class DataSourceValidationError:
	"""Validation errors collected for a single source row."""

	row_number: int | None
	messages: list[str]

	def to_message(self) -> str:
		"""Format the validation error for user-facing reporting."""
		joined_messages = "; ".join(self.messages)
		if self.row_number is None:
			return joined_messages
		return f"Row {self.row_number}: {joined_messages}"


@dataclass(slots=True)
class DataSourceResult:
	"""Normalized output from a data source operation."""

	records: list[DataSourceRecord] = field(default_factory=list)
	validation_errors: list[DataSourceValidationError] = field(default_factory=list)
	source_failure_reason: str | None = None
	source_failure_details: str | None = None

	@property
	def successful_rows(self) -> int:
		"""Return the count of successfully normalized rows."""
		return len(self.records)

	@property
	def failed_rows(self) -> int:
		"""Return the number of failed rows or source-level failures."""
		return len(self.validation_errors) + (1 if self.source_failure_reason else 0)


class BaseDataSource(ABC):
	"""Small interface for sources that return normalized product DTOs."""

	@abstractmethod
	def load(self, source: str | Path) -> DataSourceResult:
		"""Load source data and normalize it into ScrapedProductData records."""
