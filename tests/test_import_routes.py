"""Tests for the Flask file import page."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import re

from openpyxl import Workbook
import pytest

from app import create_app
from database.db import db, initialize_database
from database.models import Listing, PriceHistory, Product, Seller
from services.import_service import ImportServiceError


@pytest.fixture
def client(tmp_path: Path):
	app = create_app(
		{
			"TESTING": True,
			"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
			"IMPORT_STATE_DIR": tmp_path / "import-state",
			"MAX_CONTENT_LENGTH": 10 * 1024 * 1024,
		}
	)
	with app.app_context():
		initialize_database(app)
		with app.test_client() as test_client:
			yield test_client
		db.session.remove()
		db.drop_all()
		db.engine.dispose()


def build_csv_upload(content: str, filename: str = "products.csv") -> dict[str, object]:
	return {
		"action": "preview",
		"import_file": (BytesIO(content.encode("utf-8")), filename),
	}


def build_xlsx_upload(filename: str = "products.xlsx") -> dict[str, object]:
	buffer = BytesIO()
	workbook = Workbook()
	worksheet = workbook.active
	worksheet.append(["platform", "product_name", "current_price"])
	worksheet.append(["hepsiburada", "Casio Vintage A159", "2499,90"])
	workbook.save(buffer)
	workbook.close()
	buffer.seek(0)
	return {
		"action": "preview",
		"import_file": (buffer, filename),
	}


def extract_preview_token(response_text: str) -> str:
	match = re.search(r'name="preview_token" value="([a-f0-9]+)"', response_text)
	assert match is not None
	return match.group(1)


def test_import_page_get_opens(client) -> None:
	response = client.get("/import")

	assert response.status_code == 200
	assert b"Import Data" in response.data


def test_csv_upload_is_accepted(client) -> None:
	response = client.post(
		"/import",
		data=build_csv_upload("platform,product_name,current_price\ntrendyol,Example Watch,7499.90\n"),
		content_type="multipart/form-data",
	)

	assert response.status_code == 200
	assert b"Preview / Validation" in response.data
	assert b"Valid rows" in response.data


def test_xlsx_upload_is_accepted(client) -> None:
	response = client.post(
		"/import",
		data=build_xlsx_upload(),
		content_type="multipart/form-data",
	)

	assert response.status_code == 200
	assert b"Casio Vintage A159" in response.data


def test_unsupported_extension_is_rejected(client) -> None:
	response = client.post(
		"/import",
		data=build_csv_upload("fake", filename="products.exe"),
		content_type="multipart/form-data",
	)

	assert response.status_code == 200
	assert b"Only CSV and XLSX files are supported" in response.data


def test_empty_upload_is_rejected(client) -> None:
	response = client.post(
		"/import",
		data={"action": "preview", "import_file": (BytesIO(b""), "")},
		content_type="multipart/form-data",
	)

	assert response.status_code == 200
	assert b"Select a CSV or XLSX file before previewing" in response.data


def test_invalid_rows_are_shown_in_preview(client) -> None:
	response = client.post(
		"/import",
		data=build_csv_upload(
			"platform,product_name,current_price\ntrendyol,Bad Watch,not-a-price\n"
		),
		content_type="multipart/form-data",
	)

	assert response.status_code == 200
	assert b"invalid current_price format" in response.data


def test_valid_rows_are_shown_in_preview(client) -> None:
	response = client.post(
		"/import",
		data=build_csv_upload(
			"platform,product_name,current_price,seller_name\ntrendyol,Good Watch,100,Example Seller\n"
		),
		content_type="multipart/form-data",
	)

	assert response.status_code == 200
	assert b"Good Watch" in response.data
	assert b"Import Valid Rows" in response.data


def test_preview_does_not_write_to_database(client) -> None:
	client.post(
		"/import",
		data=build_csv_upload("platform,product_name,current_price\ntrendyol,Preview Only,100\n"),
		content_type="multipart/form-data",
	)

	assert Product.query.count() == 0
	assert Listing.query.count() == 0


def test_import_valid_rows_writes_to_database(client) -> None:
	preview_response = client.post(
		"/import",
		data=build_csv_upload(
			"platform,product_name,current_price,seller_name\ntrendyol,Import Watch,100,Example Seller\n"
		),
		content_type="multipart/form-data",
	)

	token = extract_preview_token(preview_response.get_data(as_text=True))
	import_response = client.post("/import", data={"action": "import", "preview_token": token})

	assert import_response.status_code == 200
	assert b"Import Completed" in import_response.data
	assert Product.query.count() == 1
	assert Listing.query.count() == 1
	assert PriceHistory.query.count() == 1


def test_invalid_rows_are_not_written_to_database(client) -> None:
	preview_response = client.post(
		"/import",
		data=build_csv_upload(
			"platform,product_name,current_price,seller_name\ntrendyol,Bad Watch,invalid,Example Seller\ntrendyol,Good Watch,100,Example Seller\n"
		),
		content_type="multipart/form-data",
	)

	token = extract_preview_token(preview_response.get_data(as_text=True))
	client.post("/import", data={"action": "import", "preview_token": token})

	assert Product.query.count() == 1
	assert Listing.query.count() == 1
	assert PriceHistory.query.count() == 1


def test_reimport_does_not_duplicate_product_seller_or_listing(client) -> None:
	data = build_csv_upload(
		"platform,product_name,current_price,seller_name,brand,model\ntrendyol,Repeat Watch,100,Example Seller,Casio,GA-2100\n"
	)
	preview_one = client.post("/import", data=data, content_type="multipart/form-data")
	token_one = extract_preview_token(preview_one.get_data(as_text=True))
	client.post("/import", data={"action": "import", "preview_token": token_one})

	preview_two = client.post(
		"/import",
		data=build_csv_upload(
			"platform,product_name,current_price,seller_name,brand,model\ntrendyol,Repeat Watch,100,Example Seller,Casio,GA-2100\n"
		),
		content_type="multipart/form-data",
	)
	token_two = extract_preview_token(preview_two.get_data(as_text=True))
	client.post("/import", data={"action": "import", "preview_token": token_two})

	assert Product.query.count() == 1
	assert Seller.query.count() == 1
	assert Listing.query.count() == 1


def test_reimport_adds_price_history_observation(client) -> None:
	preview_one = client.post(
		"/import",
		data=build_csv_upload("platform,product_name,current_price\ntrendyol,History Watch,100\n"),
		content_type="multipart/form-data",
	)
	token_one = extract_preview_token(preview_one.get_data(as_text=True))
	client.post("/import", data={"action": "import", "preview_token": token_one})

	preview_two = client.post(
		"/import",
		data=build_csv_upload("platform,product_name,current_price\ntrendyol,History Watch,100\n"),
		content_type="multipart/form-data",
	)
	token_two = extract_preview_token(preview_two.get_data(as_text=True))
	client.post("/import", data={"action": "import", "preview_token": token_two})

	assert PriceHistory.query.count() == 2


def test_uploaded_filename_path_traversal_is_sanitized(client) -> None:
	response = client.post(
		"/import",
		data=build_csv_upload("platform,product_name,current_price\ntrendyol,Safe Watch,100\n", filename="../../../evil.csv"),
		content_type="multipart/form-data",
	)

	body = response.get_data(as_text=True)
	assert "../../../evil.csv" not in body
	assert "evil.csv" in body


def test_uploaded_html_cell_content_is_escaped(client) -> None:
	response = client.post(
		"/import",
		data=build_csv_upload(
			"platform,product_name,current_price\ntrendyol,<script>alert(1)</script>,100\n"
		),
		content_type="multipart/form-data",
	)

	body = response.get_data(as_text=True)
	assert "<script>alert(1)</script>" not in body
	assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_database_failure_returns_controlled_error(client, monkeypatch: pytest.MonkeyPatch) -> None:
	preview_response = client.post(
		"/import",
		data=build_csv_upload("platform,product_name,current_price\ntrendyol,Failure Watch,100\n"),
		content_type="multipart/form-data",
	)
	token = extract_preview_token(preview_response.get_data(as_text=True))

	def fake_commit_import(preview_token: str, *, state_dir=None):
		raise ImportServiceError("Database error occurred during import.")

	monkeypatch.setattr("app.routes.commit_import", fake_commit_import)
	response = client.post("/import", data={"action": "import", "preview_token": token})

	assert response.status_code == 200
	assert b"Database error occurred during import." in response.data


def test_large_upload_returns_controlled_error(client) -> None:
	client.application.config["MAX_CONTENT_LENGTH"] = 256
	large_content = "platform,product_name,current_price\n" + ("trendyol,Large Watch,100\n" * 40)
	response = client.post("/import", data=build_csv_upload(large_content), content_type="multipart/form-data")

	assert response.status_code == 413
	assert b"The uploaded file is too large" in response.data