"""Sequential first-page catalog aggregation; no query pagination or APIs."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from threading import Lock
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from discovery.base import BaseDiscoveryProvider
from discovery.models import CatalogOutcome, DiscoveredProduct, DiscoveryResult
from scrapers.fetchers import FetchResult, FetcherError
from scrapers.saatvesaat_scraper import SaatVeSaatScraper
from services.product_identity_service import canonical_product_url, product_identity


@dataclass(frozen=True, slots=True)
class CatalogTarget:
    display_name: str
    path: str
    kind: str = "brand"

    def __post_init__(self):
        parts = urlsplit(self.path)
        if (not self.path.startswith("/") or self.path.startswith("//")
                or parts.scheme or parts.netloc or parts.query or parts.fragment
                or any(value in self.path for value in ("%", "\\", "..", "?", "#"))
                or any(ord(value) < 33 for value in self.path)):
            raise ValueError("Catalog targets must be plain safe relative paths.")

    @property
    def id(self) -> str:
        return self.path.lstrip("/")


# Observed in public homepage navigation on 2026-09-06. Keep all URLs here;
# recheck robots at runtime. Order favors distinct brands before overlapping ones.
CATALOG_TARGETS = (
    CatalogTarget("Fossil", "/fossil"),
    CatalogTarget("Seiko", "/seiko"),
    CatalogTarget("Timex", "/timex"),
    CatalogTarget("Skagen", "/skagen"),
    CatalogTarget("Emporio Armani", "/emporio-armani"),
    CatalogTarget("Diesel", "/diesel"),
    CatalogTarget("Guess", "/guess"),
    CatalogTarget("Lacoste", "/lacoste"),
    CatalogTarget("Tommy Hilfiger", "/tommy-hilfiger"),
    CatalogTarget("Michael Kors", "/michael-kors"),
    CatalogTarget("Welder", "/welder"),
    CatalogTarget("Wesse", "/wesse"),
    CatalogTarget("Versace", "/versace"),
    CatalogTarget("Calvin Klein", "/calvin-klein"),
    CatalogTarget("DKNY", "/dkny"),
    CatalogTarget("Esprit", "/esprit"),
    CatalogTarget("Jacques Philippe", "/jacques-philippe"),
    CatalogTarget("Raymond Weil", "/raymond-weil"),
    CatalogTarget("Maurice Lacroix", "/maurice-lacroix"),
    CatalogTarget("Oris", "/oris"),
    CatalogTarget("Armani Exchange", "/armani-exchange"),
    CatalogTarget("Police", "/police"),
    CatalogTarget("Philipp Plein", "/philipp-plein"),
    CatalogTarget("Boss Watches", "/boss-watches"),
    CatalogTarget("Seiko 5", "/seiko-5"),
)
DEFAULT_TARGET_PRODUCTS = 100
DEFAULT_MAX_CATALOGS = 15
MAX_TARGET_PRODUCTS = 200
MAX_CATALOGS = 25
_run_lock = Lock()


class CatalogFetcher:
    """Fetch server-rendered cards without following unchecked redirects."""

    def fetch(self, url, *, user_agent, timeout_seconds, headless=True):
        try:
            response = requests.get(url, headers={"User-Agent": user_agent},
                                    timeout=timeout_seconds, allow_redirects=False)
        except requests.RequestException as error:
            raise FetcherError("navigation_error", "Catalog request failed.") from error
        return FetchResult(response.text, response.url, response.status_code)


class SaatVeSaatDiscoveryProvider(BaseDiscoveryProvider):
    platform = "saatvesaat"
    ALLOWED_HOSTS = frozenset(SaatVeSaatScraper.ALLOWED_HOSTS)
    ROOT = "https://www.saatvesaat.com.tr"

    def __init__(self, **kwargs):
        kwargs.setdefault("fetcher", CatalogFetcher())
        super().__init__(**kwargs)

    def build_discovery_url(self, brand: str, page: int) -> str:
        targets = [target for target in CATALOG_TARGETS if target.display_name.casefold() == brand.casefold()]
        if page != 1 or not targets:
            raise ValueError("Choose a known catalog; only its first page is supported.")
        return self.ROOT + targets[0].path

    def is_product_url(self, url: str) -> bool:
        return SaatVeSaatScraper.is_supported_url(url)

    def extract_external_product_id(self, url: str) -> str | None:
        return SaatVeSaatScraper.extract_external_product_id(url)

    def is_discovery_url(self, url: str) -> bool:
        parts = urlsplit(url)
        return (super().is_discovery_url(url) and not parts.query and not parts.fragment
                and not parts.username and not parts.password and parts.port in (None, 443)
                and parts.path in {target.path for target in CATALOG_TARGETS})

    def discover(self, brand: str, *, max_pages: int, max_products: int) -> DiscoveryResult:
        targets = [target.id for target in CATALOG_TARGETS if target.display_name.casefold() == brand.casefold()]
        return self.discover_catalogs(targets, target_products=max_products, max_catalogs=max_pages)

    def parse_discovery_page(self, html: str, source_url: str) -> list[DiscoveredProduct]:
        soup = BeautifulSoup(html, "html.parser")
        products = []
        for card in soup.select(".products-grid .product-item[data-product-id]"):
            link = card.select_one("a.product-item-photo[href]") or card.select_one("a.name[href]")
            url = self.normalize_product_url(link.get("href", ""), source_url) if link else None
            if not url:
                continue
            url = canonical_product_url(url)
            name = self._select_text(card, ("a.name",))
            if not name:
                continue
            brand = self._select_text(card, ("a.brand",))
            if brand and not name.casefold().startswith(brand.casefold()):
                name = f"{brand} {name}"
            price_node = card.select_one('[data-price-type="specialPrice"][data-price-amount]')
            price = None
            if price_node:
                try:
                    amount = Decimal(price_node["data-price-amount"])
                    if amount.is_finite() and amount >= 0:
                        price = amount.quantize(Decimal("0.01"))
                except (InvalidOperation, ValueError):
                    pass
            products.append(DiscoveredProduct(
                platform=self.platform, product_url=url,
                external_product_id=self.extract_external_product_id(url), product_name=name,
                brand=brand, current_price=price, currency="TRY" if price is not None else None,
                image_url=self._extract_image_url(card, source_url), discovery_source_url=source_url,
            ))
        return products

    def normalize_product_url(self, href: str, source_url: str) -> str | None:
        # Validate the original address before shared normalization removes its authority.
        try:
            absolute = urljoin(source_url, href.strip())
            parts = urlsplit(absolute)
            if parts.username or parts.password or parts.port not in (None, 80, 443):
                return None
            normalized = super().normalize_product_url(href, source_url)
            return canonical_product_url(normalized) if normalized else None
        except ValueError:
            return None

    def discover_catalogs(self, catalog_ids: list[str] | None = None, *,
                          target_products=DEFAULT_TARGET_PRODUCTS, max_catalogs=DEFAULT_MAX_CATALOGS) -> DiscoveryResult:
        """Aggregate only allowlisted catalogs; limits also apply to direct callers."""
        def invalid(reason, message):
            return DiscoveryResult(self.platform, "Selected catalogs", "failed", None,
                                   failure_reason=reason, message=message)
        try:
            # Reject fractional numbers and booleans instead of truncating them.
            if isinstance(target_products, bool) or isinstance(max_catalogs, bool):
                raise ValueError()
            target = int(str(target_products))
            limit = int(str(max_catalogs))
            if not 25 <= target <= MAX_TARGET_PRODUCTS or not 1 <= limit <= MAX_CATALOGS:
                raise ValueError()
        except (ValueError, TypeError):
            return invalid("invalid_limits", "Choose 25–200 products and 1–25 catalogs.")
        available = {entry.id: entry for entry in CATALOG_TARGETS}
        chosen = list(available) if catalog_ids is None else list(dict.fromkeys(catalog_ids))
        if not chosen or any(key not in available for key in chosen):
            return invalid("invalid_catalogs", "Select at least one catalog from the available list.")
        if not _run_lock.acquire(blocking=False):
            return invalid("discovery_busy", "Another catalog discovery is running. Wait for it to finish.")
        try:
            return self._collect([available[key] for key in chosen], target, limit)
        finally:
            _run_lock.release()

    def _collect(self, catalogs, target, limit):
        products = {}
        outcomes = []
        stop = "catalog_limit" if len(catalogs) > limit else "catalog_pool_exhausted"
        for catalog in catalogs[:limit]:
            url = self.ROOT + catalog.path
            reason = None
            if not self.is_discovery_url(url):
                outcomes.append(CatalogOutcome(catalog.id, catalog.display_name, "skipped", "unsafe_catalog"))
                continue
            rules = self.robots_manager.load_rules(url)
            if rules is None:
                outcomes.append(CatalogOutcome(catalog.id, catalog.display_name, "skipped", "robots_load_failed"))
                stop = "robots_load_failed"
                break
            if not rules.can_fetch(self.user_agent, url):
                outcomes.append(CatalogOutcome(catalog.id, catalog.display_name, "skipped", "robots_disallowed"))
                continue
            self.wait_for_request_slot(url)
            self.mark_request_started()
            try:
                result = self.fetcher.fetch(url, user_agent=self.user_agent,
                                            timeout_seconds=self.timeout_seconds, headless=True)
                if result.status_code in (401, 403, 429) or self._looks_blocked(result.html, result.page_title):
                    reason = "blocked_by_platform"
                elif result.final_url != url or result.status_code != 200:
                    reason = "navigation_error"
                if reason:
                    raise FetcherError(reason, reason)
                page_products = self.parse_discovery_page(result.html, url)
                if not page_products:
                    # No verified empty-state marker yet: don't mislabel changed markup as exhaustion.
                    raise FetcherError("parsing_error", "No valid product cards were present.")
            except FetcherError as error:
                reason = "blocked_by_platform" if error.reason in ("http_forbidden", "rate_limited", "blocked_by_platform") else error.reason
                outcomes.append(CatalogOutcome(catalog.id, catalog.display_name, "failed", reason))
                if reason == "blocked_by_platform":
                    stop = reason
                    break
                continue
            except Exception:
                outcomes.append(CatalogOutcome(catalog.id, catalog.display_name, "failed", "parsing_error"))
                continue
            added = duplicates = 0
            for product in page_products:
                identity = product_identity(product.product_url)
                if identity in products:
                    duplicates += 1
                    continue
                products[identity] = product
                added += 1
                if len(products) >= target:
                    break
            outcomes.append(CatalogOutcome(catalog.id, catalog.display_name, "success", None,
                                           len(page_products), added, duplicates))
            if len(products) >= target:
                stop = "target_reached"
                break
        failures = any(item.status != "success" for item in outcomes)
        status = "success" if stop == "target_reached" and not failures else "partial" if products else "failed"
        messages = {
            "target_reached": "Requested product target reached.",
            "catalog_limit": "Catalog limit reached. Increase the limit or choose other catalogs for more products.",
            "catalog_pool_exhausted": "Selected catalog pool exhausted before reaching the target.",
            "blocked_by_platform": "The platform blocked access. Discovery stopped; collected products remain available.",
            "robots_load_failed": "Robots rules could not be verified. Discovery stopped safely.",
        }
        return DiscoveryResult(
            self.platform, "Selected catalogs", status, self.ROOT,
            products=tuple(products.values()), pages_scanned=sum(item.status == "success" for item in outcomes),
            failure_reason=stop if status == "failed" else None, message=messages[stop],
            catalogs_requested=len(catalogs), catalog_limit=limit, target_products=target,
            stop_reason=stop, catalog_outcomes=tuple(outcomes),
            selected_catalog_ids=tuple(catalog.id for catalog in catalogs),
        )
