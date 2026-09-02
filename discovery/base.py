"""Shared robots-aware, sequential discovery behavior."""

from __future__ import annotations

from abc import ABC, abstractmethod
import logging
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag
import soupsieve

from config.settings import Config
from discovery.models import DiscoveredProduct, DiscoveryResult
from scrapers.base_scraper import BaseScraper
from scrapers.fetchers import BaseFetcher, FetcherError, PlaywrightFetcher
from scrapers.product_page_scraper import ProductPageScraper


logger = logging.getLogger(__name__)


class BaseDiscoveryProvider(BaseScraper, ABC):
	"""Collect public product-card metadata without persistence."""

	ALLOWED_HOSTS: frozenset[str] = frozenset()
	CARD_SELECTORS: tuple[str, ...] = ()
	NAME_SELECTORS: tuple[str, ...] = ()
	BRAND_SELECTORS: tuple[str, ...] = ()
	PRICE_SELECTORS: tuple[str, ...] = ()
	IMAGE_SELECTORS: tuple[str, ...] = ("img",)
	BLOCK_MARKERS = ("access denied", "captcha", "forbidden", "unusual traffic", "erişim engellendi")

	def __init__(
		self,
		*,
		robots_manager=None,
		fetcher: BaseFetcher | None = None,
		user_agent: str | None = None,
		default_request_delay: int | float | None = None,
		timeout_seconds: int | float | None = None,
	) -> None:
		super().__init__(
			robots_manager=robots_manager,
			user_agent=user_agent,
			default_request_delay=default_request_delay,
		)
		self.fetcher = fetcher or PlaywrightFetcher()
		self.timeout_seconds = timeout_seconds or Config.PLAYWRIGHT_TIMEOUT

	@abstractmethod
	def build_discovery_url(self, brand: str, page: int) -> str:
		"""Build one provider-controlled public discovery URL."""

	@abstractmethod
	def is_product_url(self, url: str) -> bool:
		"""Return whether a normalized URL is a provider product page."""

	@abstractmethod
	def extract_external_product_id(self, url: str) -> str | None:
		"""Extract a reliable marketplace product identifier when available."""

	def discover(self, brand: str, *, max_pages: int, max_products: int) -> DiscoveryResult:
		"""Scan bounded pages sequentially after robots approval."""
		products_by_url: dict[str, DiscoveredProduct] = {}
		pages_scanned = 0
		source_url = self.build_discovery_url(brand, 1)

		for page_number in range(1, max_pages + 1):
			page_url = self.build_discovery_url(brand, page_number)
			if not self.is_discovery_url(page_url):
				return self._failure(brand, source_url, products_by_url, pages_scanned, "discovery_parse_failed", "The provider produced an unsafe discovery URL.")

			robots_parser = self.robots_manager.load_rules(page_url)
			if robots_parser is None:
				return self._failure(brand, source_url, products_by_url, pages_scanned, "robots_load_failed", "robots.txt could not be loaded. No discovery request was sent.")
			if not robots_parser.can_fetch(self.user_agent, page_url):
				return self._failure(brand, source_url, products_by_url, pages_scanned, "robots_denied", "robots.txt denied this discovery page. No request was sent.")

			self.wait_for_request_slot(page_url)
			self.mark_request_started()
			try:
				fetch_result = self.fetcher.fetch(
					page_url,
					user_agent=self.user_agent,
					timeout_seconds=self.timeout_seconds,
					headless=True,
				)
			except FetcherError as error:
				reason = self._normalize_fetch_failure(error.reason)
				return self._failure(brand, source_url, products_by_url, pages_scanned, reason, self._failure_message(reason))
			except Exception:
				logger.exception("Unexpected %s discovery fetch failure for %s", self.platform, page_url)
				return self._failure(
					brand,
					source_url,
					products_by_url,
					pages_scanned,
					"discovery_parse_failed",
					"The discovery page could not be fetched or parsed safely.",
				)

			if fetch_result.status_code in {403, 429} or self._looks_blocked(fetch_result.html, fetch_result.page_title):
				return self._failure(brand, source_url, products_by_url, pages_scanned, "blocked_by_platform", "The marketplace blocked the public discovery request.")

			pages_scanned += 1
			try:
				page_products = self.parse_discovery_page(fetch_result.html, page_url)
			except Exception:
				logger.exception("Failed to parse %s discovery page %s", self.platform, page_url)
				return self._failure(brand, source_url, products_by_url, pages_scanned, "discovery_parse_failed", "The discovery page could not be parsed safely.")

			if not page_products:
				break
			for product in page_products:
				products_by_url.setdefault(product.product_url, product)
				if len(products_by_url) >= max_products:
					break
			if len(products_by_url) >= max_products:
				break

		products = tuple(list(products_by_url.values())[:max_products])
		message = "Discovery completed." if products else "No products were found on the allowed discovery pages."
		return DiscoveryResult(
			platform=self.platform,
			brand=brand,
			status="success",
			source_url=source_url,
			products=products,
			pages_scanned=pages_scanned,
			message=message,
		)

	def is_discovery_url(self, url: str) -> bool:
		parts = urlsplit(url)
		return parts.scheme == "https" and (parts.hostname or "").lower() in self.ALLOWED_HOSTS

	def parse_discovery_page(self, html: str, source_url: str) -> list[DiscoveredProduct]:
		"""Extract supported product anchors and optional visible card fields."""
		soup = BeautifulSoup(html, "html.parser")
		products: list[DiscoveredProduct] = []
		for anchor in soup.find_all("a", href=True):
			product_url = self.normalize_product_url(str(anchor.get("href")), source_url)
			if product_url is None:
				continue
			card = self._find_card(anchor)
			name = self._clean_text(anchor.get("title")) or self._select_text(card, self.NAME_SELECTORS)
			brand = self._select_text(card, self.BRAND_SELECTORS)
			price_text = self._select_text(card, self.PRICE_SELECTORS)
			price = ProductPageScraper.normalize_money(price_text)
			image_url = self._extract_image_url(card, source_url)
			products.append(
				DiscoveredProduct(
					platform=self.platform,
					product_url=product_url,
					external_product_id=self.extract_external_product_id(product_url),
					product_name=name,
					brand=brand,
					current_price=price,
					currency="TRY" if price is not None and price_text and ("TL" in price_text.upper() or "TRY" in price_text.upper()) else None,
					image_url=image_url,
					discovery_source_url=source_url,
				)
			)
		return products

	def normalize_product_url(self, href: str, source_url: str) -> str | None:
		absolute_url = urljoin(source_url, href.strip())
		parts = urlsplit(absolute_url)
		if parts.scheme not in {"http", "https"} or (parts.hostname or "").lower() not in self.ALLOWED_HOSTS:
			return None
		canonical = urlunsplit(("https", (parts.hostname or "").lower(), parts.path, "", ""))
		return canonical if self.is_product_url(canonical) else None

	def _find_card(self, anchor: Tag) -> Tag:
		for parent in [anchor, *list(anchor.parents)[:5]]:
			if not isinstance(parent, Tag):
				continue
			if any(soupsieve.match(selector, parent) for selector in self.CARD_SELECTORS):
				return parent
		return anchor

	def _extract_image_url(self, card: Tag, source_url: str) -> str | None:
		for selector in self.IMAGE_SELECTORS:
			image = card.select_one(selector)
			if image is None:
				continue
			raw_url = image.get("data-src") or image.get("src")
			if not isinstance(raw_url, str) or not raw_url.strip():
				continue
			absolute_url = urljoin(source_url, raw_url.strip())
			parts = urlsplit(absolute_url)
			if parts.scheme in {"http", "https"}:
				return absolute_url
		return None

	@staticmethod
	def _select_text(card: Tag, selectors: tuple[str, ...]) -> str | None:
		for selector in selectors:
			element = card.select_one(selector)
			if element is not None:
				value = BaseDiscoveryProvider._clean_text(element.get_text(" ", strip=True))
				if value:
					return value
		return None

	@staticmethod
	def _clean_text(value: object) -> str | None:
		if not isinstance(value, str):
			return None
		cleaned = " ".join(value.split())
		return cleaned or None

	def _looks_blocked(self, html: str, title: str | None) -> bool:
		content = f"{title or ''} {html[:5000]}".lower()
		return any(marker in content for marker in self.BLOCK_MARKERS)

	@staticmethod
	def _normalize_fetch_failure(reason: str) -> str:
		if reason in {"timeout", "browser_timeout"}:
			return "timeout"
		if reason in {"http_forbidden", "rate_limited"}:
			return "blocked_by_platform"
		return "discovery_parse_failed"

	@staticmethod
	def _failure_message(reason: str) -> str:
		messages = {
			"timeout": "The discovery page timed out.",
			"blocked_by_platform": "The marketplace blocked the public discovery request.",
			"discovery_parse_failed": "The discovery page could not be fetched or parsed safely.",
		}
		return messages[reason]

	def _failure(
		self,
		brand: str,
		source_url: str,
		products: dict[str, DiscoveredProduct],
		pages_scanned: int,
		reason: str,
		message: str,
	) -> DiscoveryResult:
		return DiscoveryResult(
			platform=self.platform,
			brand=brand,
			status="failed",
			source_url=source_url,
			products=tuple(products.values()),
			pages_scanned=pages_scanned,
			failure_reason=reason,
			message=message,
		)
