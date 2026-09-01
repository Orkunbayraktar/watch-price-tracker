"""Tests for file import preview and commit services."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from werkzeug.datastructures import FileStorage

from app import create_app
from database.db import db, initialize_database
from services.import_service import ImportServiceError, commit_import, preview_import


def build_upload(filename: str, content: str) -> FileStorage:
	return FileStorage(stream=BytesIO(content.encode("utf-8")), filename=filename)


@pytest.fixture
def app_context() -> None:
	app = create_app(
		{
			"TESTING": True,
			"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
		}
	)
	context = app.app_context()
	context.push()
	initialize_database(app)
	yield
	db.session.remove()
	db.drop_all()
	db.engine.dispose()
	context.pop()


def test_preview_import_deletes_temporary_upload_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	seen_paths: list[Path] = []

	def fake_load(self, source: str | Path):
		path = Path(source)
		seen_paths.append(path)
		assert path.exists() is True
		from data_sources.base import DataSourceResult
		return DataSourceResult()

	monkeypatch.setattr("data_sources.file_source.FileDataSource.load", fake_load)

	preview_import(build_upload("products.csv", "platform,product_name,current_price\ntrendyol,Watch,100\n"), state_dir=tmp_path)

	assert len(seen_paths) == 1
	assert seen_paths[0].exists() is False


def test_commit_import_deletes_preview_state_file(tmp_path: Path, app_context: None) -> None:
	preview = preview_import(
		build_upload("products.csv", "platform,product_name,current_price\ntrendyol,Watch,100\n"),
		state_dir=tmp_path,
	)

	assert preview.import_token is not None
	state_file = tmp_path / f"{preview.import_token}.json"
	assert state_file.exists() is True

	summary = commit_import(preview.import_token, state_dir=tmp_path)

	assert summary.imported_rows == 1

	assert state_file.exists() is False


def test_preview_import_rejects_unsupported_extension(tmp_path: Path) -> None:
	with pytest.raises(ImportServiceError, match="Only CSV and XLSX files are supported"):
		preview_import(build_upload("products.zip", "fake"), state_dir=tmp_path)