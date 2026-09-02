"""Hepsiburada public search discovery provider."""

from __future__ import annotations

from urllib.parse import urlencode

from discovery.base import BaseDiscoveryProvider
from scrapers.hepsiburada_scraper import HepsiburadaScraper


class HepsiburadaDiscoveryProvider(BaseDiscoveryProvider):
	platform = "hepsiburada"
	ALLOWED_HOSTS = frozenset(HepsiburadaScraper.ALLOWED_HOSTS)
	CARD_SELECTORS = ("[data-test-id='product-card']", ".productListContent-item", "li")
	NAME_SELECTORS = ("[data-test-id='product-card-name']", "h3", ".product-title")
	BRAND_SELECTORS = ("[data-test-id='product-brand']", ".product-brand")
	PRICE_SELECTORS = ("[data-test-id='price-current-price']", ".price", ".product-price")

	def build_discovery_url(self, brand: str, page: int) -> str:
		return f"https://www.hepsiburada.com/ara?{urlencode({'q': brand, 'sayfa': page})}"

	def is_product_url(self, url: str) -> bool:
		return HepsiburadaScraper.is_supported_url(url)

	def extract_external_product_id(self, url: str) -> str | None:
		return HepsiburadaScraper.extract_external_product_id(url)
