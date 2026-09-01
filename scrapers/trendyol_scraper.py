"""Single-product Trendyol scraping proof of concept."""

from __future__ import annotations

import re

from scrapers.product_page_scraper import ProductPageScraper


class TrendyolScraper(ProductPageScraper):
	"""Small, robots-aware scraper for a single Trendyol product page."""

	platform = "trendyol"
	ALLOWED_HOSTS = {"www.trendyol.com", "trendyol.com"}
	PRODUCT_NAME_SELECTORS = (
		"h1[data-testid='product-name']",
		"h1.pr-new-br",
	)
	CURRENT_PRICE_SELECTORS = (
		"[data-testid='price-current-price']",
		".prc-dsc",
	)
	OLD_PRICE_SELECTORS = (
		"[data-testid='price-old-price']",
		".prc-org",
	)
	DISCOUNT_SELECTORS = (
		"[data-testid='discount-rate']",
		".discount-ratio",
	)
	SELLER_NAME_SELECTORS = (
		"[data-testid='seller-name']",
		".seller-name-text",
	)
	SELLER_RATING_SELECTORS = (
		"[data-testid='seller-rating']",
		".seller-rating-text",
	)
	AVAILABILITY_SELECTORS = (
		"[data-testid='availability-text']",
	)
	BRAND_SELECTORS = (
		"[data-testid='product-brand']",
	)
	PRODUCT_ID_PATTERN = re.compile(r"-p-(\d+)(?:$|[/?#])")
