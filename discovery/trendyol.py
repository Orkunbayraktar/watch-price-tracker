"""Trendyol public brand/search discovery provider."""

from __future__ import annotations

from urllib.parse import urlencode

from discovery.base import BaseDiscoveryProvider
from scrapers.trendyol_scraper import TrendyolScraper


class TrendyolDiscoveryProvider(BaseDiscoveryProvider):
	platform = "trendyol"
	ALLOWED_HOSTS = frozenset(TrendyolScraper.ALLOWED_HOSTS)
	CARD_SELECTORS = (".p-card-wrppr", ".p-card-chldrn-cntnr", "[data-testid='product-card']")
	NAME_SELECTORS = (".prdct-desc-cntnr-name", ".product-name", "[data-testid='product-name']")
	BRAND_SELECTORS = (".prdct-desc-cntnr-ttl", ".product-brand", "[data-testid='product-brand']")
	PRICE_SELECTORS = (".prc-box-dscntd", ".prc-box-sllng", ".price-item", "[data-testid='price-current-price']")

	def build_discovery_url(self, brand: str, page: int) -> str:
		return f"https://www.trendyol.com/sr?{urlencode({'q': brand, 'pi': page})}"

	def is_product_url(self, url: str) -> bool:
		return TrendyolScraper.is_supported_url(url) and self.extract_external_product_id(url) is not None

	def extract_external_product_id(self, url: str) -> str | None:
		return TrendyolScraper.extract_external_product_id(url)
