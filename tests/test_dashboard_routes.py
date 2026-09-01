"""Tests for dashboard, product, and seller analytics routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from database.db import db, initialize_database
from database.models import Listing, PriceHistory, Product, Seller


@pytest.fixture
def client(tmp_path: Path):
	app = create_app(
		{
			"TESTING": True,
			"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
			"IMPORT_STATE_DIR": tmp_path / "import-state",
		}
	)
	app_context = app.app_context()
	app_context.push()
	initialize_database(app)
	with app.test_client() as test_client:
		yield test_client
	db.session.remove()
	db.drop_all()
	db.engine.dispose()
	app_context.pop()


def seed_dashboard_data() -> dict[str, int]:
	now = datetime.now(timezone.utc)
	product_one = Product(brand="Casio", model="A159", name="Casio Retro")
	product_two = Product(brand="Seiko", model="SNK809", name="Seiko Ocean")
	product_three = Product(brand="Citizen", model="AW123", name="Citizen Field")
	seller_one = Seller(platform="trendyol", name="Market Time", rating=Decimal("4.60"))
	seller_two = Seller(platform="hepsiburada", name="Ocean Store", rating=Decimal("4.80"))
	seller_three = Seller(platform="trendyol", name="<script>alert(1)</script> Seller", rating=None)
	db.session.add_all([product_one, product_two, product_three, seller_one, seller_two, seller_three])
	db.session.flush()

	listing_one = Listing(
		product=product_one,
		seller=seller_one,
		platform="trendyol",
		external_product_id="TR-1",
		url="https://example.com/tr-1",
		current_price=Decimal("4200.00"),
		old_price=Decimal("4500.00"),
		discount_percentage=Decimal("6.67"),
		currency="TRY",
		availability="in_stock",
		last_scraped_at=now - timedelta(hours=4),
	)
	listing_two = Listing(
		product=product_one,
		seller=seller_two,
		platform="hepsiburada",
		external_product_id="HB-1",
		url="https://example.com/hb-1",
		current_price=Decimal("4100.00"),
		old_price=None,
		discount_percentage=None,
		currency="TRY",
		availability="in_stock",
		last_scraped_at=now - timedelta(hours=1),
	)
	listing_three = Listing(
		product=product_two,
		seller=seller_two,
		platform="hepsiburada",
		external_product_id="HB-2",
		url="https://example.com/hb-2",
		current_price=Decimal("3800.00"),
		old_price=Decimal("3999.00"),
		discount_percentage=Decimal("4.98"),
		currency="TRY",
		availability="limited",
		last_scraped_at=now - timedelta(days=1),
	)
	listing_four = Listing(
		product=product_three,
		seller=seller_three,
		platform="trendyol",
		external_product_id="TR-3",
		url="https://example.com/tr-3",
		current_price=Decimal("5200.00"),
		old_price=None,
		discount_percentage=None,
		currency="TRY",
		availability="in_stock",
		last_scraped_at=now - timedelta(days=2),
	)
	db.session.add_all([listing_one, listing_two, listing_three, listing_four])
	db.session.flush()

	db.session.add_all(
		[
			PriceHistory(listing=listing_one, price=Decimal("4200.00"), old_price=Decimal("4500.00"), discount_percentage=Decimal("6.67"), recorded_at=now - timedelta(hours=4)),
			PriceHistory(listing=listing_two, price=Decimal("4100.00"), old_price=None, discount_percentage=None, recorded_at=now - timedelta(hours=1)),
			PriceHistory(listing=listing_three, price=Decimal("3800.00"), old_price=Decimal("3999.00"), discount_percentage=Decimal("4.98"), recorded_at=now - timedelta(days=1)),
			PriceHistory(listing=listing_four, price=Decimal("5200.00"), old_price=None, discount_percentage=None, recorded_at=now - timedelta(days=2)),
		]
	)
	db.session.commit()
	return {
		"product_one_id": product_one.id,
		"product_two_id": product_two.id,
		"seller_one_id": seller_one.id,
		"seller_two_id": seller_two.id,
	}


def test_dashboard_loads(client) -> None:
	response = client.get("/")

	assert response.status_code == 200
	assert b"Dashboard" in response.data


def test_empty_dashboard_renders_useful_empty_state(client) -> None:
	response = client.get("/")
	body = response.get_data(as_text=True)

	assert "No product data yet. Import a CSV or Excel file to start tracking prices." in body
	assert "0" in body


def test_dashboard_metrics_use_real_database_values(client) -> None:
	seed_dashboard_data()
	response = client.get("/")
	body = response.get_data(as_text=True)

	assert "Total Products" in body
	assert "3" in body
	assert "Total Sellers" in body
	assert "4.100,00 TL" in body
	assert "Recently Updated Listings" in body


def test_products_page_loads(client) -> None:
	seed_dashboard_data()
	response = client.get("/products")

	assert response.status_code == 200
	assert b"Product Catalog" in response.data


def test_product_search_works(client) -> None:
	seed_dashboard_data()
	response = client.get("/products?q=Ocean")
	body = response.get_data(as_text=True)

	assert "Seiko Ocean" in body
	assert "Casio Retro" not in body


def test_brand_filter_works(client) -> None:
	seed_dashboard_data()
	response = client.get("/products?brand=Casio")
	body = response.get_data(as_text=True)

	assert "Casio Retro" in body
	assert "Seiko Ocean" not in body


def test_platform_filter_works(client) -> None:
	seed_dashboard_data()
	response = client.get("/products?platform=trendyol")
	body = response.get_data(as_text=True)

	assert "Casio Retro" in body
	assert "Citizen Field" in body
	assert "Seiko Ocean" not in body


def test_product_sorting_works(client) -> None:
	seed_dashboard_data()
	response = client.get("/products?sort=price_asc")
	body = response.get_data(as_text=True)

	assert body.find("Seiko Ocean") < body.find("Casio Retro")


def test_product_pagination_works(client) -> None:
	for index in range(26):
		product = Product(brand="Brand", model=f"M-{index}", name=f"Product {index:02d}")
		db.session.add(product)
		db.session.flush()
		db.session.add(
			Listing(
				product=product,
				seller=None,
				platform="imported",
				external_product_id=f"IMPORT-{index}",
				url=f"import://imported/IMPORT-{index}",
				current_price=Decimal("100.00") + Decimal(index),
				currency="TRY",
			)
		)
	db.session.commit()

	response = client.get("/products?sort=name&page=2")
	body = response.get_data(as_text=True)

	assert "Page 2 of 2" in body
	assert "Product 20" in body
	assert "Product 00" not in body


def test_product_detail_loads(client) -> None:
	ids = seed_dashboard_data()
	response = client.get(f"/products/{ids['product_one_id']}")
	body = response.get_data(as_text=True)

	assert response.status_code == 200
	assert "Price History" in body
	assert "Seller Comparison" in body
	assert "Observation History" in body
	assert "Price History" in body
	assert "Selected Series" in body


def test_invalid_product_id_returns_404(client) -> None:
	response = client.get("/products/9999")

	assert response.status_code == 404


def test_sellers_page_loads(client) -> None:
	seed_dashboard_data()
	response = client.get("/sellers")

	assert response.status_code == 200
	assert b"Seller Directory" in response.data


def test_seller_search_works(client) -> None:
	seed_dashboard_data()
	response = client.get("/sellers?q=Ocean")
	body = response.get_data(as_text=True)

	assert "Ocean Store" in body
	assert "Market Time" not in body


def test_seller_platform_filter_works(client) -> None:
	seed_dashboard_data()
	response = client.get("/sellers?platform=trendyol")
	body = response.get_data(as_text=True)

	assert "Market Time" in body
	assert "Ocean Store" not in body


def test_seller_detail_loads(client) -> None:
	ids = seed_dashboard_data()
	response = client.get(f"/sellers/{ids['seller_two_id']}")
	body = response.get_data(as_text=True)

	assert response.status_code == 200
	assert "Seller Product Listings" in body
	assert "Seiko Ocean" in body


def test_filters_do_not_allow_unsafe_query_behavior(client) -> None:
	seed_dashboard_data()
	response = client.get("/products?q=' OR 1=1 --&brand=' OR 1=1 --&platform=' OR 1=1 --")

	assert response.status_code == 200


def test_database_text_is_escaped_in_products_page(client) -> None:
	seed_dashboard_data()
	response = client.get("/sellers")
	body = response.get_data(as_text=True)

	assert "<script>alert(1)</script>" not in body
	assert "&lt;script&gt;alert(1)&lt;/script&gt; Seller" in body