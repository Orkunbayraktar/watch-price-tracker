"""Single-product Saat&Saat scraping proof of concept."""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from scrapers.product_page_scraper import ProductPageScraper


class SaatVeSaatScraper(ProductPageScraper):
	"""Robots-aware parser for public Saat&Saat product pages."""

	platform = "saatvesaat"
	ALLOWED_HOSTS = {"www.saatvesaat.com.tr", "saatvesaat.com.tr"}
	URL_PRODUCT_PATTERN = re.compile(r"-p-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*/?$", re.IGNORECASE)
	PRODUCT_ID_PATTERN = re.compile(r"-p-([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)/?$", re.IGNORECASE)
	MODEL_PATTERN = re.compile(r"\b[A-Z]{1,10}[A-Z0-9]*(?:-[A-Z0-9]+)*\b")
	PRODUCT_NAME_SELECTORS = (
		"h1[itemprop='name']",
		"[data-testid='product-name']",
		".product-detail-name",
		".product-detail__name",
		"main h1",
		"h1",
	)
	CURRENT_PRICE_SELECTORS = (
		"[data-testid='product-price']",
		"[data-testid='current-price']",
		".product-detail-price .current-price",
		".product-price .current-price",
		".product-price-new",
		".price-new",
		".current-price",
	)
	OLD_PRICE_SELECTORS = (
		"[data-testid='old-price']",
		".product-detail-price .old-price",
		".product-price .old-price",
		".product-price-old",
		".price-old",
		".old-price",
	)
	DISCOUNT_SELECTORS = (
		"[data-testid='discount-rate']",
		".discount-rate",
		".discount-percentage",
	)
	SELLER_NAME_SELECTORS = (
		"[data-testid='seller-name']",
		"[itemprop='seller'] [itemprop='name']",
		".seller-name",
		".merchant-name",
	)
	SELLER_RATING_SELECTORS = (
		"[data-testid='seller-rating']",
		".seller-rating",
		".merchant-rating",
	)
	AVAILABILITY_SELECTORS = (
		"[data-testid='availability']",
		"[itemprop='availability']",
		".product-availability",
		".availability",
	)
	BRAND_SELECTORS = (
		"[data-testid='product-brand']",
		"[itemprop='brand']",
		".product-brand",
	)

	@classmethod
	def extract_external_product_id(cls, url: str) -> str | None:
		"""Return the stable product code exposed by Saat&Saat product URLs."""
		external_product_id = super().extract_external_product_id(url)
		return external_product_id.upper() if external_product_id else None

	def _extract_seller_name(self, soup: BeautifulSoup, offers: dict[str, Any] | None) -> str | None:
		"""Create a seller only when a seller name is visible on the product page."""
		del offers
		return self._select_first_text(soup, self.SELLER_NAME_SELECTORS)
