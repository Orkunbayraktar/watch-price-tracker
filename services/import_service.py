"""Persistence orchestration for generic normalized data sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from data_sources.base import BaseDataSource, DataSourceValidationError
from services.persistence_service import PersistenceError, PersistenceResult, save_scraped_product


@dataclass(slots=True)
class ImportSummary:
	"""Summary of a full data source import run."""

	successful_rows: int = 0
	failed_rows: int = 0
	validation_errors: list[str] = field(default_factory=list)
	persistence_results: list[PersistenceResult] = field(default_factory=list)


def import_from_data_source(data_source: BaseDataSource, source: str | Path) -> ImportSummary:
	"""Load normalized data from a source and persist valid rows."""
	load_result = data_source.load(source)
	summary = ImportSummary()

	for error in load_result.validation_errors:
		summary.failed_rows += 1
		summary.validation_errors.append(error.to_message())

	if load_result.source_failure_reason is not None:
		summary.failed_rows += 1
		details = load_result.source_failure_details or "The data source did not return normalized product data."
		summary.validation_errors.append(
			f"Source failure ({load_result.source_failure_reason}): {details}"
		)

	for record in load_result.records:
		try:
			result = save_scraped_product(record.data)
		except PersistenceError as error:
			summary.failed_rows += 1
			row_label = f"Row {record.row_number}" if record.row_number is not None else "Source item"
			summary.validation_errors.append(f"{row_label}: persistence failed: {error}")
			continue

		if result is None:
			summary.failed_rows += 1
			row_label = f"Row {record.row_number}" if record.row_number is not None else "Source item"
			summary.validation_errors.append(
				f"{row_label}: persistence rejected the normalized product data."
			)
			continue

		summary.successful_rows += 1
		summary.persistence_results.append(result)

	return summary