"""Tests for the database layer."""

from decimal import Decimal
import unittest

from app import create_app
from database.db import db, initialize_database
from database.models import Listing, PriceHistory, Product, Seller


class DatabaseModelTests(unittest.TestCase):
	"""Verify core database models and relationships."""

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

	def test_product_can_be_created(self) -> None:
		product = Product(brand="Casio", model="GA-2100-1A1", name="Casio G-Shock GA-2100")

		db.session.add(product)
		db.session.commit()

		saved_product = Product.query.one()
		self.assertEqual(saved_product.brand, "Casio")
		self.assertEqual(saved_product.model, "GA-2100-1A1")

	def test_seller_can_be_created(self) -> None:
		seller = Seller(
			platform="trendyol",
			external_seller_id="seller-001",
			name="Example Watch Store",
			rating=Decimal("4.80"),
		)

		db.session.add(seller)
		db.session.commit()

		saved_seller = Seller.query.one()
		self.assertEqual(saved_seller.platform, "trendyol")
		self.assertEqual(saved_seller.name, "Example Watch Store")
		self.assertEqual(saved_seller.rating, Decimal("4.80"))

	def test_listing_can_be_created_from_product_and_seller(self) -> None:
		product = Product(brand="Casio", model="GA-2100-1A1", name="Casio G-Shock GA-2100")
		seller = Seller(platform="hepsiburada", name="Example Watch Store")
		listing = Listing(
			product=product,
			seller=seller,
			platform="hepsiburada",
			url="https://example.com/listing/casio-ga-2100",
			current_price=Decimal("7499.90"),
			old_price=Decimal("7999.90"),
			discount_percentage=Decimal("6.25"),
			currency="TRY",
			availability="in_stock",
			visible_sales_count=None,
		)

		db.session.add(listing)
		db.session.commit()

		saved_listing = Listing.query.one()
		self.assertEqual(saved_listing.product.name, "Casio G-Shock GA-2100")
		self.assertEqual(saved_listing.seller.platform, "hepsiburada")
		self.assertEqual(saved_listing.current_price, Decimal("7499.90"))

	def test_price_history_can_be_added_to_listing(self) -> None:
		listing = self._create_listing()
		history_entry = PriceHistory(
			listing=listing,
			price=Decimal("7499.90"),
			old_price=Decimal("7999.90"),
			discount_percentage=Decimal("6.25"),
		)

		db.session.add(history_entry)
		db.session.commit()

		saved_history = PriceHistory.query.one()
		self.assertEqual(saved_history.listing.id, listing.id)
		self.assertEqual(saved_history.price, Decimal("7499.90"))

	def test_relationships_work_in_both_directions(self) -> None:
		listing = self._create_listing()
		history_entry = PriceHistory(listing=listing, price=Decimal("7499.90"))

		db.session.add(history_entry)
		db.session.commit()

		saved_product = Product.query.one()
		saved_seller = Seller.query.one()
		saved_listing = Listing.query.one()
		self.assertEqual(len(saved_product.listings), 1)
		self.assertEqual(len(saved_seller.listings), 1)
		self.assertEqual(len(saved_listing.price_history), 1)

	def test_prices_are_stored_as_decimal_values(self) -> None:
		listing = self._create_listing(current_price=Decimal("7499.90"))

		self.assertIsInstance(listing.current_price, Decimal)
		self.assertEqual(listing.current_price, Decimal("7499.90"))

	def _create_listing(self, current_price: Decimal = Decimal("7499.90")) -> Listing:
		product = Product(brand="Casio", model="GA-2100-1A1", name="Casio G-Shock GA-2100")
		seller = Seller(platform="trendyol", name="Example Watch Store")
		listing = Listing(
			product=product,
			seller=seller,
			platform="trendyol",
			url="https://example.com/listing/casio-ga-2100",
			current_price=current_price,
			old_price=Decimal("7999.90"),
			discount_percentage=Decimal("6.25"),
			currency="TRY",
			availability="in_stock",
		)

		db.session.add(listing)
		db.session.commit()

		return listing


if __name__ == "__main__":
	unittest.main()
