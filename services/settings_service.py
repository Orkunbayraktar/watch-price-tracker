"""Validated persistence for the small set of editable application settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from flask import current_app

from database.db import db
from database.models import AppSetting


class SettingsServiceError(RuntimeError):
	"""Base error for editable settings operations."""


class SettingsValidationError(SettingsServiceError):
	"""Raised when one or more submitted setting values are invalid."""

	def __init__(self, field_errors: dict[str, str]) -> None:
		super().__init__("Review the highlighted settings and try again.")
		self.field_errors = field_errors


@dataclass(frozen=True, slots=True)
class SettingDefinition:
	key: str
	config_key: str
	label: str
	minimum: int
	maximum: int
	unit: str
	help_text: str


@dataclass(frozen=True, slots=True)
class ApplicationSettings:
	data_quality_stale_hours: int
	discovery_max_pages: int
	discovery_max_products: int

	def to_dict(self) -> dict[str, int]:
		return {
			"data_quality_stale_hours": self.data_quality_stale_hours,
			"discovery_max_pages": self.discovery_max_pages,
			"discovery_max_products": self.discovery_max_products,
		}


SETTING_DEFINITIONS = (
	SettingDefinition(
		"data_quality_stale_hours",
		"DATA_QUALITY_STALE_HOURS",
		"Data Quality stale threshold",
		1,
		8760,
		"hours",
		"Active products become stale after this many hours without a successful scrape.",
	),
	SettingDefinition(
		"discovery_max_pages",
		"DISCOVERY_MAX_PAGES",
		"Discovery page limit",
		1,
		10,
		"pages",
		"Maximum public discovery pages scanned sequentially for one request.",
	),
	SettingDefinition(
		"discovery_max_products",
		"DISCOVERY_MAX_PRODUCTS",
		"Discovery product limit",
		1,
		200,
		"products",
		"Default preview limit; the existing hard safety ceiling remains 200 products.",
	),
)
DEFINITIONS_BY_KEY = {definition.key: definition for definition in SETTING_DEFINITIONS}


def get_application_settings() -> ApplicationSettings:
	"""Return persisted editable values with validated config fallbacks."""
	rows = AppSetting.query.filter(AppSetting.key.in_(DEFINITIONS_BY_KEY)).all()
	persisted = {row.key: row.value for row in rows}
	values = {
		definition.key: _coerce_or_default(persisted.get(definition.key), definition)
		for definition in SETTING_DEFINITIONS
	}
	return ApplicationSettings(**values)


def get_setting_value(key: str) -> int:
	"""Return one allowlisted effective integer setting."""
	definition = DEFINITIONS_BY_KEY.get(key)
	if definition is None:
		raise KeyError(f"Unsupported editable setting: {key}")
	row = db.session.get(AppSetting, key)
	return _coerce_or_default(row.value if row is not None else None, definition)


def update_application_settings(values: Mapping[str, object]) -> ApplicationSettings:
	"""Validate the complete editable settings form before persisting changes."""
	validated, errors = _validate_values(values)
	if errors:
		raise SettingsValidationError(errors)

	try:
		for key, value in validated.items():
			row = db.session.get(AppSetting, key)
			if row is None:
				db.session.add(AppSetting(key=key, value=str(value)))
			else:
				row.value = str(value)
		db.session.commit()
	except Exception as error:
		db.session.rollback()
		raise SettingsServiceError("Application settings could not be saved.") from error
	return get_application_settings()


def restore_default_settings() -> ApplicationSettings:
	"""Remove editable overrides so configured defaults become effective again."""
	try:
		AppSetting.query.filter(AppSetting.key.in_(DEFINITIONS_BY_KEY)).delete(synchronize_session=False)
		db.session.commit()
	except Exception as error:
		db.session.rollback()
		raise SettingsServiceError("Default settings could not be restored.") from error
	return get_application_settings()


def _validate_values(values: Mapping[str, object]) -> tuple[dict[str, int], dict[str, str]]:
	validated: dict[str, int] = {}
	errors: dict[str, str] = {}
	for definition in SETTING_DEFINITIONS:
		raw_value = values.get(definition.key)
		try:
			value = int(str(raw_value).strip())
		except (TypeError, ValueError):
			errors[definition.key] = "Enter a whole number."
			continue
		if value < definition.minimum or value > definition.maximum:
			errors[definition.key] = f"Enter a value from {definition.minimum} to {definition.maximum}."
			continue
		validated[definition.key] = value
	return validated, errors


def _coerce_or_default(value: object, definition: SettingDefinition) -> int:
	default = current_app.config.get(definition.config_key, definition.minimum)
	try:
		default_value = int(default)
	except (TypeError, ValueError):
		default_value = definition.minimum
	default_value = min(definition.maximum, max(definition.minimum, default_value))
	try:
		candidate = int(str(value).strip()) if value is not None else default_value
	except (TypeError, ValueError):
		return default_value
	return candidate if definition.minimum <= candidate <= definition.maximum else default_value
