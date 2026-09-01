"""Persistence orchestration for generic normalized data sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import tempfile
from typing import Any
from uuid import uuid4

from data_sources.base import BaseDataSource, DataSourceRecord, DataSourceValidationError
from data_sources.file_source import FileDataSource
from scrapers.models import ScrapedProductData
from services.persistence_service import PersistenceError, PersistenceResult, save_scraped_product
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename


class ImportServiceError(RuntimeError):
	"""Raised when a preview or import operation cannot be completed safely."""


@dataclass(slots=True)
class ImportSummary:
	"""Summary of a full data source import run."""

	processed_rows: int = 0
	imported_rows: int = 0
	failed_rows: int = 0
	validation_errors: list[str] = field(default_factory=list)
	persistence_results: list[PersistenceResult] = field(default_factory=list)
	product_created_count: int = 0
	product_matched_count: int = 0
	seller_created_count: int = 0
	seller_matched_count: int = 0
	listing_created_count: int = 0
	listing_updated_count: int = 0
	price_observations_added: int = 0

	@property
	def successful_rows(self) -> int:
		"""Backward-compatible alias for imported row count."""
		return self.imported_rows


@dataclass(slots=True)
class PreviewImportResult:
	"""Preview information returned before committing a file import."""

	safe_filename: str
	total_rows: int
	valid_rows: int
	invalid_rows: int
	preview_records: list[DataSourceRecord] = field(default_factory=list)
	validation_errors: list[str] = field(default_factory=list)
	import_token: str | None = None

	@property
	def can_import(self) -> bool:
		"""Return whether preview produced at least one valid row for import."""
		return self.import_token is not None and self.valid_rows > 0


def import_from_data_source(data_source: BaseDataSource, source: str | Path) -> ImportSummary:
	"""Load normalized data from a source and persist valid rows."""
	load_result = data_source.load(source)
	return _persist_load_result(load_result.validation_errors, load_result.source_failure_reason, load_result.source_failure_details, load_result.records)


def preview_import(
	uploaded_file: FileStorage,
	*,
	state_dir: str | Path | None = None,
	preview_limit: int = 20,
) -> PreviewImportResult:
	"""Parse an uploaded file, collect validation output, and store valid rows server-side."""
	safe_filename = secure_filename(uploaded_file.filename or "")
	if not safe_filename:
		raise ImportServiceError("No file was selected.")

	suffix = Path(safe_filename).suffix.lower()
	if suffix not in FileDataSource.SUPPORTED_EXTENSIONS:
		raise ImportServiceError("Only CSV and XLSX files are supported.")

	resolved_state_dir = _resolve_state_dir(state_dir)
	resolved_state_dir.mkdir(parents=True, exist_ok=True)
	temp_upload_path = _create_temporary_upload_path(resolved_state_dir, suffix)

	try:
		uploaded_file.save(temp_upload_path)
		load_result = FileDataSource().load(temp_upload_path)
	finally:
		_cleanup_path(temp_upload_path)

	validation_errors = [error.to_message() for error in load_result.validation_errors]
	if load_result.source_failure_reason is not None:
		details = load_result.source_failure_details or "The file could not be parsed into normalized product data."
		validation_errors.append(f"Source failure ({load_result.source_failure_reason}): {details}")

	total_rows = len(load_result.records) + len(load_result.validation_errors)
	import_token = None
	if load_result.records:
		import_token = _store_preview_state(load_result.records, safe_filename, resolved_state_dir)

	return PreviewImportResult(
		safe_filename=safe_filename,
		total_rows=total_rows,
		valid_rows=len(load_result.records),
		invalid_rows=len(load_result.validation_errors),
		preview_records=load_result.records[:preview_limit],
		validation_errors=validation_errors,
		import_token=import_token,
	)


def commit_import(preview_token: str, *, state_dir: str | Path | None = None) -> ImportSummary:
	"""Persist previously previewed valid rows and clean up the temporary preview state."""
	if not preview_token or not preview_token.strip():
		raise ImportServiceError("The import preview token is missing.")

	resolved_state_dir = _resolve_state_dir(state_dir)
	state_path = resolved_state_dir / f"{preview_token}.json"
	if not state_path.exists():
		raise ImportServiceError("The import preview expired or was not found.")

	try:
		records = _load_preview_state(state_path)
		return _persist_load_result([], None, None, records)
	finally:
		_cleanup_path(state_path)


def _persist_load_result(
	validation_errors: list[DataSourceValidationError],
	source_failure_reason: str | None,
	source_failure_details: str | None,
	records: list[DataSourceRecord],
) -> ImportSummary:
	summary = ImportSummary()

	for error in validation_errors:
		summary.failed_rows += 1
		summary.validation_errors.append(error.to_message())

	if source_failure_reason is not None:
		summary.failed_rows += 1
		details = source_failure_details or "The data source did not return normalized product data."
		summary.validation_errors.append(
			f"Source failure ({source_failure_reason}): {details}"
		)

	for record in records:
		summary.processed_rows += 1
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

		summary.imported_rows += 1
		summary.persistence_results.append(result)
		if result.product_created:
			summary.product_created_count += 1
		else:
			summary.product_matched_count += 1
		if result.seller is not None:
			if result.seller_created:
				summary.seller_created_count += 1
			else:
				summary.seller_matched_count += 1
		if result.listing_created:
			summary.listing_created_count += 1
		else:
			summary.listing_updated_count += 1
		summary.price_observations_added += 1

	return summary


def _resolve_state_dir(state_dir: str | Path | None) -> Path:
	if state_dir is None:
		return Path(tempfile.gettempdir()) / "watch-price-tracker-imports"
	return Path(state_dir)


def _create_temporary_upload_path(state_dir: Path, suffix: str) -> Path:
	state_dir.mkdir(parents=True, exist_ok=True)
	temp_file = tempfile.NamedTemporaryFile(dir=state_dir, prefix="upload-", suffix=suffix, delete=False)
	temp_path = Path(temp_file.name)
	temp_file.close()
	return temp_path


def _store_preview_state(records: list[DataSourceRecord], safe_filename: str, state_dir: Path) -> str:
	token = uuid4().hex
	state_payload = {
		"safe_filename": safe_filename,
		"records": [
			{
				"row_number": record.row_number,
				"data": _serialize_scraped_product_data(record.data),
			}
			for record in records
		],
	}
	state_path = state_dir / f"{token}.json"
	state_path.write_text(json.dumps(state_payload, ensure_ascii=True), encoding="utf-8")
	return token


def _load_preview_state(state_path: Path) -> list[DataSourceRecord]:
	try:
		state_payload = json.loads(state_path.read_text(encoding="utf-8"))
	except (OSError, json.JSONDecodeError) as error:
		raise ImportServiceError("The saved import preview could not be read.") from error

	records_payload = state_payload.get("records")
	if not isinstance(records_payload, list):
		raise ImportServiceError("The saved import preview is invalid.")

	records: list[DataSourceRecord] = []
	for item in records_payload:
		if not isinstance(item, dict) or "data" not in item:
			raise ImportServiceError("The saved import preview is invalid.")
		records.append(
			DataSourceRecord(
				data=_deserialize_scraped_product_data(item["data"]),
				row_number=item.get("row_number"),
			)
		)
	return records


def _serialize_scraped_product_data(data: ScrapedProductData) -> dict[str, Any]:
	return {
		"platform": data.platform,
		"product_name": data.product_name,
		"brand": data.brand,
		"model": data.model,
		"current_price": _serialize_decimal(data.current_price),
		"old_price": _serialize_decimal(data.old_price),
		"discount_percentage": _serialize_decimal(data.discount_percentage),
		"currency": data.currency,
		"seller_name": data.seller_name,
		"seller_rating": _serialize_decimal(data.seller_rating),
		"availability": data.availability,
		"product_url": data.product_url,
		"external_product_id": data.external_product_id,
		"scraped_at": data.scraped_at.isoformat(),
		"visible_sales_count": data.visible_sales_count,
	}


def _deserialize_scraped_product_data(payload: dict[str, Any]) -> ScrapedProductData:
	return ScrapedProductData(
		platform=str(payload["platform"]),
		product_name=payload.get("product_name"),
		brand=payload.get("brand"),
		model=payload.get("model"),
		current_price=_deserialize_decimal(payload.get("current_price")),
		old_price=_deserialize_decimal(payload.get("old_price")),
		discount_percentage=_deserialize_decimal(payload.get("discount_percentage")),
		currency=payload.get("currency"),
		seller_name=payload.get("seller_name"),
		seller_rating=_deserialize_decimal(payload.get("seller_rating")),
		availability=payload.get("availability"),
		product_url=str(payload["product_url"]),
		external_product_id=payload.get("external_product_id"),
		scraped_at=datetime.fromisoformat(str(payload["scraped_at"])),
		visible_sales_count=payload.get("visible_sales_count"),
	)


def _serialize_decimal(value: Decimal | None) -> str | None:
	if value is None:
		return None
	return str(value)


def _deserialize_decimal(value: Any) -> Decimal | None:
	if value is None:
		return None
	return Decimal(str(value))


def _cleanup_path(path: Path) -> None:
	try:
		path.unlink(missing_ok=True)
	except OSError:
		return