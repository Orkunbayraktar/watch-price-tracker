"""Tests for scraper-to-database persistence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from sqlalchemy.exc import SQLAlchemyError

from app import create_app
from database.db import db, initialize_database
from database.models import Listing, PriceHistory, Product, Seller
from scrapers.models import ScrapedProductData
from services.persistence_service import PersistenceError, save_scraped_product
from services.scraping_service import scrape_and_save_product


class PersistenceServiceTests(unittest.TestCase):
	"""Verify deduplication, upsert behavior, and transaction safety."""

	def setUp(self) -> None:
		self.app = create_app(
			{
				"TESTING": True,
				"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
			}
		)
		self.app_context = self.app.app_context()
		self.app_context.push()
		initialize_database(self.app)

	def tearDown(self) -> None:
		db.session.remove()
		db.drop_all()
		db.engine.dispose()
		self.app_context.pop()

	def test_first_save_creates_product_seller_listing_and_price_history(self) -> None:
		result = save_scraped_product(self._make_data())

		self.assertIsNotNone(result)
		self.assertEqual(Product.query.count(), 1)
		self.assertEqual(Seller.query.count(), 1)
		self.assertEqual(Listing.query.count(), 1)
		self.assertEqual(PriceHistory.query.count(), 1)

	def test_saving_same_data_twice_is_idempotent_for_main_records(self) -> None:
		first = save_scraped_product(self._make_data())
		second = save_scraped_product(self._make_data(scraped_at=first.price_history.recorded_at + timedelta(hours=1)))

		self.assertIsNotNone(second)
		self.assertEqual(Product.query.count(), 1)
		self.assertEqual(Seller.query.count(), 1)
		self.assertEqual(Listing.query.count(), 1)
		self.assertEqual(PriceHistory.query.count(), 2)

	def test_same_listing_with_new_price_updates_listing_and_preserves_history(self) -> None:
		first = save_scraped_product(self._make_data(current_price=Decimal("7499.90")))
		second = save_scraped_product(
			self._make_data(
				current_price=Decimal("6999.90"),
				old_price=Decimal("7499.90"),
				scraped_at=first.price_history.recorded_at + timedelta(hours=2),
			)
		)

		listing = Listing.query.one()
		history_prices = [entry.price for entry in PriceHistory.query.order_by(PriceHistory.recorded_at).all()]
		self.assertEqual(Listing.query.count(), 1)
		self.assertEqual(listing.current_price, Decimal("6999.90"))
		self.assertEqual(history_prices, [Decimal("7499.90"), Decimal("6999.90")])
		self.assertEqual(second.listing.id, listing.id)

	def test_same_product_with_different_sellers_creates_two_listings(self) -> None:
		save_scraped_product(self._make_data(seller_name="Example Watch Store"))
		save_scraped_product(
			self._make_data(
				seller_name="Another Watch Store",
				scraped_at=datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc),
			)
		)

		self.assertEqual(Product.query.count(), 1)
		self.assertEqual(Seller.query.count(), 2)
		self.assertEqual(Listing.query.count(), 2)

	def test_same_seller_name_on_different_platforms_does_not_merge(self) -> None:
		save_scraped_product(self._make_data(platform="trendyol", seller_name="Example Watch Store"))
		save_scraped_product(
			self._make_data(
				platform="hepsiburada",
				seller_name="Example Watch Store",
				product_url="https://www.hepsiburada.com/example-watch-p-HBCV0000123456",
				external_product_id="HBCV0000123456",
				scraped_at=datetime(2026, 8, 31, 11, 0, tzinfo=timezone.utc),
			)
		)

		self.assertEqual(Seller.query.count(), 2)

	def test_product_deduplication_uses_brand_and_model_when_present(self) -> None:
		save_scraped_product(self._make_data(brand="Casio", model="GA-2100-1A1DR", product_name="Casio G-Shock GA-2100-1A1DR"))
		save_scraped_product(
			self._make_data(
				brand="  casio  ",
				model="  ga-2100-1a1dr ",
				product_name="Casio G-SHOCK GA-2100-1A1DR Updated Title",
				scraped_at=datetime(2026, 8, 31, 10, 30, tzinfo=timezone.utc),
			)
		)

		product = Product.query.one()
		self.assertEqual(Product.query.count(), 1)
		self.assertEqual(product.brand, "Casio")
		self.assertEqual(product.model, "GA-2100-1A1DR")
		self.assertEqual(product.name, "Casio G-SHOCK GA-2100-1A1DR Updated Title")

	def test_product_deduplication_falls_back_to_brand_and_name_when_model_missing(self) -> None:
		save_scraped_product(self._make_data(model=None, product_name="Casio Vintage A168"))
		save_scraped_product(
			self._make_data(
				brand="  casio ",
				model=None,
				product_name="  casio vintage a168  ",
				scraped_at=datetime(2026, 8, 31, 10, 30, tzinfo=timezone.utc),
			)
		)

		self.assertEqual(Product.query.count(), 1)

	def test_persistence_allows_listing_without_seller_when_seller_is_missing(self) -> None:
		result = save_scraped_product(self._make_data(seller_name=None, seller_rating=None))

		listing = Listing.query.one()
		self.assertIsNotNone(result)
		self.assertEqual(Seller.query.count(), 0)
		self.assertIsNone(listing.seller_id)
		self.assertEqual(PriceHistory.query.count(), 1)

	def test_missing_seller_later_reuses_existing_listing_for_same_url(self) -> None:
		first = save_scraped_product(self._make_data())
		second = save_scraped_product(
			self._make_data(
				seller_name=None,
				seller_rating=None,
				scraped_at=first.price_history.recorded_at + timedelta(hours=1),
			)
		)

		listing = Listing.query.one()
		self.assertIsNotNone(second)
		self.assertEqual(Listing.query.count(), 1)
		self.assertEqual(Seller.query.count(), 1)
		self.assertEqual(listing.id, first.listing.id)
		self.assertEqual(PriceHistory.query.count(), 2)

	def test_scrape_and_save_product_does_not_write_when_scraper_returns_none(self) -> None:
		scraper = Mock()
		scraper.is_supported_url.return_value = True
		scraper.scrape_product.return_value = None

		result = scrape_and_save_product("https://www.trendyol.com/casio/g-shock-ga-2100-p-33139591", scraper=scraper)

		self.assertIsNone(result)
		self.assertEqual(Product.query.count(), 0)
		self.assertEqual(Seller.query.count(), 0)
		self.assertEqual(Listing.query.count(), 0)
		self.assertEqual(PriceHistory.query.count(), 0)

	def test_scrape_and_save_product_does_not_write_when_scraper_fails_with_structured_reason(self) -> None:
		scraper = Mock()
		scraper.is_supported_url.return_value = True
		scraper.scrape_product.return_value = None
		scraper.last_failure_reason = "http_forbidden"
		scraper.last_failure_details = "The server returned HTTP 403 and blocked the request."

		result = scrape_and_save_product("https://www.trendyol.com/casio/g-shock-ga-2100-p-33139591", scraper=scraper)

		self.assertIsNone(result)
		self.assertEqual(Product.query.count(), 0)
		self.assertEqual(Seller.query.count(), 0)
		self.assertEqual(Listing.query.count(), 0)
		self.assertEqual(PriceHistory.query.count(), 0)

	def test_invalid_scraped_data_does_not_change_database(self) -> None:
		result = save_scraped_product(self._make_data(current_price=None))

		self.assertIsNone(result)
		self.assertEqual(Product.query.count(), 0)
		self.assertEqual(Seller.query.count(), 0)
		self.assertEqual(Listing.query.count(), 0)
		self.assertEqual(PriceHistory.query.count(), 0)

	def test_decimal_values_are_preserved_in_listing_and_price_history(self) -> None:
		save_scraped_product(self._make_data(current_price=Decimal("7499.90"), old_price=Decimal("8199.10")))

		listing = Listing.query.one()
		price_history = PriceHistory.query.one()
		self.assertEqual(listing.current_price, Decimal("7499.90"))
		self.assertEqual(price_history.old_price, Decimal("8199.10"))

	def test_transaction_failure_rolls_back_all_pending_records(self) -> None:
		with patch.object(db.session, "flush", side_effect=SQLAlchemyError("forced failure")):
			with self.assertRaises(PersistenceError):
				save_scraped_product(self._make_data())

		self.assertEqual(Product.query.count(), 0)
		self.assertEqual(Seller.query.count(), 0)
		self.assertEqual(Listing.query.count(), 0)
		self.assertEqual(PriceHistory.query.count(), 0)

	def test_scrapers_do_not_import_database_or_sqlalchemy(self) -> None:
		scraper_dir = Path(__file__).resolve().parent.parent / "scrapers"
		for scraper_file in scraper_dir.glob("*.py"):
			file_text = scraper_file.read_text(encoding="utf-8")
			self.assertNotIn("from database", file_text, msg=str(scraper_file))
			self.assertNotIn("import database", file_text, msg=str(scraper_file))
			self.assertNotIn("sqlalchemy", file_text.lower(), msg=str(scraper_file))

	def _make_data(self, **overrides: object) -> ScrapedProductData:
		data = ScrapedProductData(
			platform="trendyol",
			product_name="Casio G-SHOCK GA-2100-1A1DR Erkek Kol Saati",
			brand="Casio",
			model="GA-2100-1A1DR",
			current_price=Decimal("7499.90"),
			old_price=Decimal("8199.90"),
			discount_percentage=Decimal("8.50"),
			currency="TRY",
			seller_name="Example Watch Store",
			seller_rating=Decimal("4.80"),
			availability="in_stock",
			product_url="https://www.trendyol.com/casio/g-shock-ga-2100-p-33139591",
			external_product_id="33139591",
			scraped_at=datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc),
			visible_sales_count=None,
		)
		for key, value in overrides.items():
			setattr(data, key, value)

		return data


if __name__ == "__main__":
	unittest.main()