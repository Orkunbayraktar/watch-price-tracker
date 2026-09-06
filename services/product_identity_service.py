"""Shared product identity without database access or outbound requests."""

from urllib.parse import urlsplit, urlunsplit

from scrapers.trendyol_scraper import TrendyolScraper
from scrapers.saatvesaat_scraper import SaatVeSaatScraper
from scrapers.hepsiburada_scraper import HepsiburadaScraper


def product_identity(url: str) -> tuple[str, str]:
    """Prefer a provider's URL product code; never trust a catalog card ID."""
    parts = urlsplit(url.strip())
    for provider in (TrendyolScraper, SaatVeSaatScraper, HepsiburadaScraper):
        if provider.is_supported_url(url):
            product_id = provider.extract_external_product_id(url)
            if product_id:
                return provider.platform, product_id
    canonical = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))
    return "url", canonical


def canonical_product_url(url: str) -> str:
    """Canonicalize ID-bearing Trendyol/Saat&Saat product URLs only."""
    parts = urlsplit(url.strip())
    # Reject credentials and unusual ports instead of silently discarding them.
    if parts.username or parts.password or parts.port not in (None, 80, 443):
        raise ValueError("Product URLs cannot contain credentials or nonstandard ports.")
    for provider in (TrendyolScraper, SaatVeSaatScraper):
        if provider.is_supported_url(url) and provider.extract_external_product_id(url):
            host = "www.trendyol.com" if provider.platform == "trendyol" else "www.saatvesaat.com.tr"
            return urlunsplit(("https", host, parts.path.rstrip("/"), "", ""))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))
