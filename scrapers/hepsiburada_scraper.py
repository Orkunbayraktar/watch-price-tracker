"""Single-product Hepsiburada scraping proof of concept."""

from __future__ import annotations

import re

from scrapers.product_page_scraper import ProductPageScraper


class HepsiburadaScraper(ProductPageScraper):
	"""Small, robots-aware scraper for a single Hepsiburada product page."""

	platform = "hepsiburada"
	ALLOWED_HOSTS = {"www.hepsiburada.com", "hepsiburada.com"}
	URL_PRODUCT_PATTERN = re.compile(r"-(?:p|pm)-[A-Za-z0-9]+(?:$|[/?#])", re.IGNORECASE)
	PRODUCT_ID_PATTERN = re.compile(r"-(?:p|pm)-([A-Za-z0-9]+)(?:$|[/?#])", re.IGNORECASE)
	PRODUCT_NAME_SELECTORS = (
		"h1[data-test-id='product-name']",
		"h1[data-test-id='product-title']",
		"h1",
	)
	CURRENT_PRICE_SELECTORS = (
		"[data-test-id='price-current-price']",
		"[data-test-id='price']",
		".price",
	)
	OLD_PRICE_SELECTORS = (
		"[data-test-id='price-old-price']",
		".old-price",
	)
	DISCOUNT_SELECTORS = (
		"[data-test-id='discount-rate']",
		".discount-rate",
	)
	SELLER_NAME_SELECTORS = (
		"[data-test-id='seller-name']",
		"[data-test-id='merchant-name']",
		".merchant-name",
	)
	SELLER_RATING_SELECTORS = (
		"[data-test-id='seller-rating']",
		".merchant-rating",
	)
	AVAILABILITY_SELECTORS = (
		"[data-test-id='availability']",
		".availability",
	)
	BRAND_SELECTORS = (
		"[data-test-id='product-brand']",
		".product-brand",
	)

	@classmethod
	def extract_external_product_id(cls, url: str) -> str | None:
		"""Extract and normalize the Hepsiburada external product identifier."""
		external_product_id = super().extract_external_product_id(url)
		if external_product_id is None:
			return None

		return external_product_id.upper()
