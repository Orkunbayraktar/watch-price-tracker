"""Typed DTOs used by the scraping layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(slots=True)
class ScrapedProductData:
	"""Normalized single-listing product data extracted from a product page."""

	platform: str
	product_name: str | None
	brand: str | None
	model: str | None
	current_price: Decimal | None
	old_price: Decimal | None
	discount_percentage: Decimal | None
	currency: str | None
	seller_name: str | None
	seller_rating: Decimal | None
	availability: str | None
	product_url: str
	external_product_id: str | None
	scraped_at: datetime
	visible_sales_count: int | None = None