"""Tests for the generic data source architecture."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from app import create_app
from data_sources.file_source import FileDataSource
from data_sources.scraper_source import WebScraperDataSource
from database.db import db, initialize_database
from database.models import Listing, PriceHistory, Product, Seller
from scrapers.hepsiburada_scraper import HepsiburadaScraper
from scrapers.models import ScrapedProductData
from scrapers.trendyol_scraper import TrendyolScraper
from services.import_service import import_from_data_source


class FakeResponse:
	"""Simple fake response object for HTTP failure tests."""

	def __init__(self, text: str, status_error: Exception | None = None) -> None:
		self.text = text
		self._status_error = status_error

	def raise_for_status(self) -> None:
		if self._status_error is not None:
			raise self._status_error


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


def write_csv_file(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
	file_path = tmp_path / "products.csv"
	fieldnames = [
		"platform",
		"product_name",
		"brand",
		"model",
		"current_price",
		"old_price",
		"discount_percentage",
		"currency",
		"seller_name",
		"seller_rating",
		"availability",
		"product_url",
		"external_product_id",
		"visible_sales_count",
	]
	with file_path.open("w", encoding="utf-8", newline="") as handle:
		writer = csv.DictWriter(handle, fieldnames=fieldnames)
		writer.writeheader()
		for row in rows:
			writer.writerow(row)
	return file_path


def write_xlsx_file(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
	from openpyxl import Workbook

	file_path = tmp_path / "products.xlsx"
	workbook = Workbook()
	worksheet = workbook.active
	headers = [
		"platform",
		"product_name",
		"brand",
		"model",
		"current_price",
		"old_price",
		"discount_percentage",
		"currency",
		"seller_name",
		"seller_rating",
		"availability",
		"product_url",
		"external_product_id",
		"visible_sales_count",
	]
	worksheet.append(headers)
	for row in rows:
		worksheet.append([row.get(header) for header in headers])
	workbook.save(file_path)
	workbook.close()
	return file_path


def build_expected_scraped_data(platform: str) -> ScrapedProductData:
	return ScrapedProductData(
		platform=platform,
		product_name="Casio G-SHOCK GA-2100",
		brand="Casio",
		model="GA-2100",
		current_price=Decimal("6999.00"),
		old_price=None,
		discount_percentage=None,
		currency="TRY",
		seller_name="Example Store",
		seller_rating=None,
		availability="in_stock",
		product_url=f"https://www.{platform}.com/example-product",
		external_product_id="EXAMPLE-1",
		scraped_at=db.func.now(),
		visible_sales_count=None,
	)


def test_csv_row_is_converted_to_scraped_product(tmp_path: Path) -> None:
	file_path = write_csv_file(
		tmp_path,
		[
			{
				"platform": "Trendyol",
				"product_name": "Casio G-SHOCK GA-2100",
				"brand": "Casio",
				"model": "GA-2100",
				"current_price": "6999.00",
				"currency": "TRY",
				"seller_name": "Example Store",
				"external_product_id": "TR-123",
			},
		],
	)

	result = FileDataSource().load(file_path)

	assert result.successful_rows == 1
	assert result.failed_rows == 0
	assert result.records[0].data.product_name == "Casio G-SHOCK GA-2100"
	assert result.records[0].data.external_product_id == "TR-123"


def test_price_is_normalized_to_decimal(tmp_path: Path) -> None:
	file_path = write_csv_file(
		tmp_path,
		[
			{
				"platform": "trendyol",
				"product_name": "Casio G-SHOCK GA-2100",
				"current_price": "7499.90",
			},
		],
	)

	result = FileDataSource().load(file_path)

	assert result.records[0].data.current_price == Decimal("7499.90")


def test_turkish_price_format_is_parsed(tmp_path: Path) -> None:
	file_path = write_csv_file(
		tmp_path,
		[
			{
				"platform": "trendyol",
				"product_name": "Casio G-SHOCK GA-2100",
				"current_price": "7.499,90 TL",
			},
		],
	)

	result = FileDataSource().load(file_path)

	assert result.records[0].data.current_price == Decimal("7499.90")


def test_platform_is_normalized(tmp_path: Path) -> None:
	file_path = write_csv_file(
		tmp_path,
		[
			{
				"platform": "TRENDYOL",
				"product_name": "Casio G-SHOCK GA-2100",
				"current_price": "6999",
			},
		],
	)

	result = FileDataSource().load(file_path)

	assert result.records[0].data.platform == "trendyol"


def test_optional_seller_can_be_none(tmp_path: Path) -> None:
	file_path = write_csv_file(
		tmp_path,
		[
			{
				"platform": "hepsiburada",
				"product_name": "Casio G-SHOCK GA-2100",
				"current_price": "6999",
			},
		],
	)

	result = FileDataSource().load(file_path)

	assert result.records[0].data.seller_name is None


def test_invalid_row_is_reported(tmp_path: Path) -> None:
	file_path = write_csv_file(
		tmp_path,
		[
			{
				"platform": "trendyol",
				"product_name": "Casio G-SHOCK GA-2100",
				"current_price": "not-a-price",
			},
		],
	)

	result = FileDataSource().load(file_path)

	assert result.successful_rows == 0
	assert result.failed_rows == 1
	assert result.validation_errors[0].to_message() == "Row 2: invalid current_price format"


def test_invalid_row_does_not_block_valid_row(tmp_path: Path) -> None:
	file_path = write_csv_file(
		tmp_path,
		[
			{
				"platform": "trendyol",
				"product_name": "Casio G-SHOCK GA-2100",
				"current_price": "invalid",
			},
			{
				"platform": "trendyol",
				"product_name": "Casio Vintage A159",
				"current_price": "2499",
			},
		],
	)

	result = FileDataSource().load(file_path)

	assert result.successful_rows == 1
	assert result.failed_rows == 1
	assert result.records[0].data.product_name == "Casio Vintage A159"


def test_same_csv_twice_does_not_duplicate_products_or_listings(tmp_path: Path, app_context: None) -> None:
	file_path = write_csv_file(
		tmp_path,
		[
			{
				"platform": "trendyol",
				"product_name": "Casio G-SHOCK GA-2100",
				"brand": "Casio",
				"model": "GA-2100",
				"current_price": "6999",
				"seller_name": "Example Store",
			},
		],
	)

	first_summary = import_from_data_source(FileDataSource(), file_path)
	second_summary = import_from_data_source(FileDataSource(), file_path)

	assert first_summary.successful_rows == 1
	assert second_summary.successful_rows == 1
	assert Product.query.count() == 1
	assert Seller.query.count() == 1
	assert Listing.query.count() == 1


def test_same_csv_twice_adds_price_history_observations(tmp_path: Path, app_context: None) -> None:
	file_path = write_csv_file(
		tmp_path,
		[
			{
				"platform": "trendyol",
				"product_name": "Casio G-SHOCK GA-2100",
				"brand": "Casio",
				"model": "GA-2100",
				"current_price": "6999",
				"seller_name": "Example Store",
			},
		],
	)

	import_from_data_source(FileDataSource(), file_path)
	import_from_data_source(FileDataSource(), file_path)

	assert PriceHistory.query.count() == 2


def test_generated_external_product_id_is_stable(tmp_path: Path) -> None:
	file_path = write_csv_file(
		tmp_path,
		[
			{
				"platform": "Trendyol",
				"product_name": "Casio G-SHOCK GA-2100",
				"brand": "Casio",
				"model": "GA-2100",
				"current_price": "6999",
				"seller_name": "Example Store",
			},
		],
	)

	first_result = FileDataSource().load(file_path)
	second_result = FileDataSource().load(file_path)

	assert first_result.records[0].data.external_product_id == second_result.records[0].data.external_product_id
	assert first_result.records[0].data.external_product_id.startswith("import-generated-")


def test_web_scraper_data_source_uses_trendyol_scraper() -> None:
	expected = build_expected_scraped_data("trendyol")
	with patch.object(TrendyolScraper, "scrape_product", return_value=expected) as scrape_mock:
		result = WebScraperDataSource().load("https://www.trendyol.com/casio-watch-p-33139591")

	assert result.successful_rows == 1
	assert result.records[0].data is expected
	scrape_mock.assert_called_once_with("https://www.trendyol.com/casio-watch-p-33139591")


def test_web_scraper_data_source_uses_hepsiburada_scraper() -> None:
	expected = build_expected_scraped_data("hepsiburada")
	with patch.object(HepsiburadaScraper, "scrape_product", return_value=expected) as scrape_mock:
		result = WebScraperDataSource().load(
			"https://www.hepsiburada.com/casio-retro-kol-saati-a159wa-n1df-pm-sacsa159wan1df"
		)

	assert result.successful_rows == 1
	assert result.records[0].data is expected
	scrape_mock.assert_called_once_with(
		"https://www.hepsiburada.com/casio-retro-kol-saati-a159wa-n1df-pm-sacsa159wan1df"
	)


def test_web_scraper_data_source_preserves_http_failure_behavior() -> None:
	robots_manager = Mock()
	parser = Mock()
	parser.can_fetch.return_value = True
	robots_manager.load_rules.return_value = parser
	session = requests.Session()
	http_error = requests.HTTPError("403 Client Error")
	http_error.response = Mock(status_code=403)
	session.get = Mock(return_value=FakeResponse("", http_error))
	scraper = TrendyolScraper(robots_manager=robots_manager, session=session)

	with patch("data_sources.scraper_source.create_scraper_for_url", return_value=scraper):
		result = WebScraperDataSource().load("https://www.trendyol.com/casio-watch-p-33139591")

	assert result.successful_rows == 0
	assert result.failed_rows == 1
	assert result.source_failure_reason == "http_forbidden"
	assert result.source_failure_details == "The server returned HTTP 403 and blocked the request."


def test_xlsx_row_is_converted_to_scraped_product(tmp_path: Path) -> None:
	file_path = write_xlsx_file(
		tmp_path,
		[
			{
				"platform": "Hepsiburada",
				"product_name": "Casio Vintage A159",
				"current_price": "2499,90",
			},
		],
	)

	result = FileDataSource().load(file_path)

	assert result.successful_rows == 1
	assert result.records[0].data.platform == "hepsiburada"
	assert result.records[0].data.current_price == Decimal("2499.90")