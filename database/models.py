"""SQLAlchemy data models for the Watch Price Tracker project."""

from datetime import datetime, timezone

from database.db import db


def utc_now() -> datetime:
	"""Return the current UTC timestamp."""
	return datetime.now(timezone.utc)


class Product(db.Model):
	"""Normalized product catalog entry."""

	__tablename__ = "products"

	id = db.Column(db.Integer, primary_key=True)
	brand = db.Column(db.String(120), nullable=False)
	model = db.Column(db.String(120), nullable=True)
	name = db.Column(db.String(255), nullable=False)
	created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
	updated_at = db.Column(
		db.DateTime(timezone=True),
		nullable=False,
		default=utc_now,
		onupdate=utc_now,
	)

	listings = db.relationship(
		"Listing",
		back_populates="product",
		cascade="all, delete-orphan",
	)


class Seller(db.Model):
	"""Marketplace seller entity."""

	__tablename__ = "sellers"
	__table_args__ = (
		db.UniqueConstraint("platform", "external_seller_id", name="uq_seller_platform_external_id"),
	)

	id = db.Column(db.Integer, primary_key=True)
	platform = db.Column(db.String(50), nullable=False, index=True)
	external_seller_id = db.Column(db.String(120), nullable=True)
	name = db.Column(db.String(255), nullable=False)
	rating = db.Column(db.Numeric(4, 2), nullable=True)
	created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
	updated_at = db.Column(
		db.DateTime(timezone=True),
		nullable=False,
		default=utc_now,
		onupdate=utc_now,
	)

	listings = db.relationship(
		"Listing",
		back_populates="seller",
		cascade="all, delete-orphan",
	)


class Listing(db.Model):
	"""Marketplace listing for a product sold by a seller."""

	__tablename__ = "listings"

	id = db.Column(db.Integer, primary_key=True)
	product_id = db.Column(db.Integer, db.ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
	seller_id = db.Column(db.Integer, db.ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False)
	platform = db.Column(db.String(50), nullable=False, index=True)
	external_product_id = db.Column(db.String(120), nullable=True)
	url = db.Column(db.Text, nullable=False)
	current_price = db.Column(db.Numeric(10, 2), nullable=False)
	old_price = db.Column(db.Numeric(10, 2), nullable=True)
	discount_percentage = db.Column(db.Numeric(5, 2), nullable=True)
	currency = db.Column(db.String(8), nullable=False, default="TRY")
	availability = db.Column(db.String(50), nullable=True)
	visible_sales_count = db.Column(db.Integer, nullable=True)
	created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
	updated_at = db.Column(
		db.DateTime(timezone=True),
		nullable=False,
		default=utc_now,
		onupdate=utc_now,
	)
	last_scraped_at = db.Column(db.DateTime(timezone=True), nullable=True)

	product = db.relationship("Product", back_populates="listings")
	seller = db.relationship("Seller", back_populates="listings")
	price_history = db.relationship(
		"PriceHistory",
		back_populates="listing",
		cascade="all, delete-orphan",
		order_by="PriceHistory.recorded_at",
	)


class PriceHistory(db.Model):
	"""Historical price snapshot for a listing."""

	__tablename__ = "price_history"

	id = db.Column(db.Integer, primary_key=True)
	listing_id = db.Column(db.Integer, db.ForeignKey("listings.id", ondelete="CASCADE"), nullable=False)
	price = db.Column(db.Numeric(10, 2), nullable=False)
	old_price = db.Column(db.Numeric(10, 2), nullable=True)
	discount_percentage = db.Column(db.Numeric(5, 2), nullable=True)
	recorded_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

	listing = db.relationship("Listing", back_populates="price_history")


class ScrapeRun(db.Model):
	"""Track metadata for future scraping runs."""

	__tablename__ = "scrape_runs"

	id = db.Column(db.Integer, primary_key=True)
	platform = db.Column(db.String(50), nullable=False, index=True)
	started_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
	finished_at = db.Column(db.DateTime(timezone=True), nullable=True)
	status = db.Column(db.String(20), nullable=False, default="running")
	products_found = db.Column(db.Integer, nullable=False, default=0)
	listings_found = db.Column(db.Integer, nullable=False, default=0)
	errors_count = db.Column(db.Integer, nullable=False, default=0)
	error_message = db.Column(db.Text, nullable=True)
