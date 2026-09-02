"""Validation and persistence tests for editable application settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import create_app
from database.db import db, initialize_database
from database.models import AppSetting
from services.settings_service import SettingsValidationError, get_application_settings, restore_default_settings, update_application_settings


@pytest.fixture
def app_context(tmp_path: Path):
	app = create_app(
		{
			"TESTING": True,
			"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
			"DATA_QUALITY_STALE_HOURS": 48,
			"DISCOVERY_MAX_PAGES": 3,
			"DISCOVERY_MAX_PRODUCTS": 100,
			"DISCOVERY_STATE_DIR": tmp_path,
		}
	)
	with app.app_context():
		initialize_database(app)
		yield app
		db.session.remove()
		db.drop_all()
		db.engine.dispose()


def test_defaults_are_read_from_current_application_config(app_context) -> None:
	settings = get_application_settings()

	assert settings.to_dict() == {
		"data_quality_stale_hours": 48,
		"discovery_max_pages": 3,
		"discovery_max_products": 100,
	}


def test_valid_settings_are_persisted(app_context) -> None:
	settings = update_application_settings(
		{"data_quality_stale_hours": "72", "discovery_max_pages": "5", "discovery_max_products": "150"}
	)

	assert settings.to_dict() == {
		"data_quality_stale_hours": 72,
		"discovery_max_pages": 5,
		"discovery_max_products": 150,
	}
	assert AppSetting.query.count() == 3


@pytest.mark.parametrize(
	"values",
	[
		{"data_quality_stale_hours": "0", "discovery_max_pages": "3", "discovery_max_products": "100"},
		{"data_quality_stale_hours": "48", "discovery_max_pages": "11", "discovery_max_products": "100"},
		{"data_quality_stale_hours": "48", "discovery_max_pages": "3", "discovery_max_products": "201"},
		{"data_quality_stale_hours": "abc", "discovery_max_pages": "3", "discovery_max_products": "100"},
	],
)
def test_invalid_settings_are_rejected_without_partial_updates(app_context, values: dict[str, str]) -> None:
	with pytest.raises(SettingsValidationError):
		update_application_settings(values)

	assert AppSetting.query.count() == 0


def test_restore_defaults_removes_only_editable_overrides(app_context) -> None:
	update_application_settings(
		{"data_quality_stale_hours": "72", "discovery_max_pages": "5", "discovery_max_products": "150"}
	)
	db.session.add(AppSetting(key="future_safe_setting", value="kept"))
	db.session.commit()

	settings = restore_default_settings()

	assert settings.to_dict() == {
		"data_quality_stale_hours": 48,
		"discovery_max_pages": 3,
		"discovery_max_products": 100,
	}
	assert db.session.get(AppSetting, "future_safe_setting").value == "kept"
