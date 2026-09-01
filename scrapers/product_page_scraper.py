"""Shared helpers for single-product marketplace scrapers."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup
import requests
from requests import Session

from config.settings import Config
from scrapers.base_scraper import BaseScraper
from scrapers.fetchers import BaseFetcher, FetchResult, FetcherError, PlaywrightFetcher, RequestsFetcher
from scrapers.models import ScrapedProductData


logger = logging.getLogger(__name__)


ALLOWED_FETCH_STRATEGIES = {"requests", "playwright"}


class ProductPageScraper(BaseScraper):
	"""Shared request and parsing behavior for single-product scrapers."""

	ALLOWED_HOSTS: set[str] = set()
	URL_PRODUCT_PATTERN: re.Pattern[str] | None = None
	PRODUCT_ID_PATTERN: re.Pattern[str] | None = None
	PRODUCT_NAME_SELECTORS: tuple[str, ...] = ()
	CURRENT_PRICE_SELECTORS: tuple[str, ...] = ()
	OLD_PRICE_SELECTORS: tuple[str, ...] = ()
	DISCOUNT_SELECTORS: tuple[str, ...] = ()
	SELLER_NAME_SELECTORS: tuple[str, ...] = ()
	SELLER_RATING_SELECTORS: tuple[str, ...] = ()
	AVAILABILITY_SELECTORS: tuple[str, ...] = ()
	BRAND_SELECTORS: tuple[str, ...] = ()
	MODEL_PATTERN = re.compile(r"\b[A-Z]{1,6}(?:-[A-Z0-9]{1,10}){1,4}\b")

	def __init__(
		self,
		robots_manager=None,
		user_agent: str | None = None,
		default_request_delay: int | float | None = None,
		session: Session | None = None,
		fetchers: dict[str, BaseFetcher] | None = None,
		debug_parser: bool = False,
	) -> None:
		super().__init__(
			robots_manager=robots_manager,
			user_agent=user_agent,
			default_request_delay=default_request_delay,
		)
		self.session = session or requests.Session()
		self.session.headers.setdefault("User-Agent", self.user_agent)
		default_fetchers: dict[str, BaseFetcher] = {
			"requests": RequestsFetcher(self.session),
			"playwright": PlaywrightFetcher(),
		}
		if fetchers:
			default_fetchers.update(fetchers)
		self.fetchers = default_fetchers
		self.debug_parser = debug_parser
		self.last_failure_reason: str | None = None
		self.last_failure_details: str | None = None

	@classmethod
	def is_supported_url(cls, url: str) -> bool:
		"""Return whether the URL belongs to a supported host and path format."""
		parsed_url = urlparse(url)
		hostname = (parsed_url.hostname or "").lower()
		if parsed_url.scheme not in {"http", "https"}:
			return False
		if hostname not in cls.ALLOWED_HOSTS:
			return False
		if cls.URL_PRODUCT_PATTERN is not None:
			return cls.URL_PRODUCT_PATTERN.search(parsed_url.path) is not None
		return True

	@classmethod
	def extract_external_product_id(cls, url: str) -> str | None:
		"""Extract the product ID from the URL if the scraper knows the pattern."""
		if cls.PRODUCT_ID_PATTERN is None:
			return None

		path = urlparse(url).path
		match = cls.PRODUCT_ID_PATTERN.search(path)
		if match is None:
			return None

		return match.group(1)

	@staticmethod
	def normalize_money(value: str | int | float | Decimal | None) -> Decimal | None:
		"""Convert Turkish money strings into Decimal values."""
		if value is None:
			return None

		if isinstance(value, Decimal):
			return value.quantize(Decimal("0.01"))

		if isinstance(value, (int, float)):
			return Decimal(str(value)).quantize(Decimal("0.01"))

		cleaned = re.sub(r"[^\d,.-]", "", value.strip())
		if not cleaned:
			return None

		if "," in cleaned and "." in cleaned:
			if cleaned.rfind(",") > cleaned.rfind("."):
				cleaned = cleaned.replace(".", "").replace(",", ".")
			else:
				cleaned = cleaned.replace(",", "")
		elif "," in cleaned:
			cleaned = cleaned.replace(".", "").replace(",", ".")
		elif cleaned.count(".") > 1:
			cleaned = cleaned.replace(".", "")
		elif "." in cleaned:
			fraction = cleaned.rsplit(".", maxsplit=1)[1]
			if len(fraction) != 2:
				cleaned = cleaned.replace(".", "")

		try:
			return Decimal(cleaned).quantize(Decimal("0.01"))
		except InvalidOperation:
			return None

	@staticmethod
	def normalize_decimal(value: str | None) -> Decimal | None:
		"""Convert generic numeric strings into Decimal values."""
		if value is None:
			return None

		cleaned = re.sub(r"[^\d,.-]", "", value.strip().replace("%", ""))
		if not cleaned:
			return None

		if "," in cleaned and "." in cleaned:
			if cleaned.rfind(",") > cleaned.rfind("."):
				cleaned = cleaned.replace(".", "").replace(",", ".")
			else:
				cleaned = cleaned.replace(",", "")
		elif "," in cleaned:
			cleaned = cleaned.replace(".", "").replace(",", ".")
		elif cleaned.count(".") > 1:
			parts = cleaned.split(".")
			cleaned = "".join(parts[:-1]) + "." + parts[-1]

		try:
			return Decimal(cleaned)
		except InvalidOperation:
			return None

	@classmethod
	def extract_model(cls, product_name: str | None) -> str | None:
		"""Extract a conservative watch model token when it is explicit in the title."""
		if not product_name:
			return None

		for candidate in cls.MODEL_PATTERN.findall(product_name.upper()):
			if any(character.isdigit() for character in candidate) and len(candidate) >= 6:
				return candidate

		return None

	def scrape_product(
		self,
		url: str,
		*,
		fetch_strategy: str = "requests",
		headed: bool = False,
	) -> ScrapedProductData | None:
		"""Fetch and parse a single product page."""
		self._clear_failure_state()
		if not self.is_supported_url(url):
			logger.warning("Unsupported %s URL: %s", self.platform, url)
			self._set_failure("unsupported_url", "The provided URL is not a supported product URL for this platform.")
			return None

		logger.info("Starting %s scrape for %s", self.platform, url)
		html = self.fetch_product_page(url, fetch_strategy=fetch_strategy, headed=headed)
		if html is None:
			return None

		return self.parse_product_page(html, url)

	def fetch_product_page(
		self,
		url: str,
		*,
		fetch_strategy: str = "requests",
		headed: bool = False,
	) -> str | None:
		"""Fetch a product page after robots.txt approval."""
		if not self.is_supported_url(url):
			logger.warning("Rejected unsupported %s URL: %s", self.platform, url)
			self._set_failure("unsupported_url", "The provided URL is not a supported product URL for this platform.")
			return None

		if fetch_strategy not in ALLOWED_FETCH_STRATEGIES:
			self._set_failure(
				"invalid_fetch_strategy",
				f"Unsupported fetch strategy: {fetch_strategy}. Allowed values are: requests, playwright.",
			)
			return None

		parser = self.robots_manager.load_rules(url)
		if parser is None:
			logger.warning("Robots.txt could not be loaded for %s", url)
			self._set_failure("robots_load_failed", "robots.txt could not be loaded. No request was sent.")
			return None

		if not parser.can_fetch(self.user_agent, url):
			logger.warning("Robots.txt denied access to %s", url)
			self._set_failure("robots_denied", "robots.txt denied access to the requested URL. No request was sent.")
			return None

		self.wait_for_request_slot(url)
		self.mark_request_started()
		fetcher = self.fetchers.get(fetch_strategy)
		if fetcher is None:
			self._set_failure(
				"invalid_fetch_strategy",
				f"No fetcher is configured for strategy: {fetch_strategy}.",
			)
			return None

		try:
			fetch_result = fetcher.fetch(
				url,
				user_agent=self.user_agent,
				timeout_seconds=Config.PLAYWRIGHT_TIMEOUT if fetch_strategy == "playwright" else Config.REQUEST_TIMEOUT,
				headless=not headed,
			)
		except FetcherError as error:
			logger.warning("%s fetch failed for %s: %s", fetch_strategy, url, error.details)
			self._set_failure(error.reason, error.details)
			return None

		if self._is_platform_blocked(fetch_result):
			if fetch_result.status_code in {403, 429}:
				self._set_failure(
					"blocked_by_platform",
					f"The platform blocked browser navigation with HTTP {fetch_result.status_code}.",
				)
			else:
				self._set_failure(
					"challenge_detected",
					"The product page appears to be an access-denied, CAPTCHA, or bot-challenge page.",
				)
			return None

		logger.info("Fetched %s product page successfully: %s", self.platform, url)
		return fetch_result.html

	def parse_product_page(self, html: str, url: str) -> ScrapedProductData | None:
		"""Parse a product page into normalized Python data."""
		self._clear_failure_state()
		soup = BeautifulSoup(html, "html.parser")
		if self._looks_like_block_page(soup):
			logger.warning("Probable anti-bot or verification page received for %s", url)
			self._set_failure("challenge_detected", "The response looks like a bot-protection, CAPTCHA, or access-denied page.")
			return None

		try:
			structured_product = self._extract_structured_product(soup, url)
			offers = self._extract_offer_data(structured_product, url)
			product_name = self._extract_product_name(soup, structured_product)
			parsed_data = ScrapedProductData(
				platform=self.platform,
				product_name=product_name,
				brand=self._extract_brand(soup, structured_product),
				model=self.extract_model(product_name),
				current_price=self._extract_current_price(soup, offers),
				old_price=self._extract_old_price(soup, offers),
				discount_percentage=self._extract_discount_percentage(soup),
				currency=self._extract_currency(offers),
				seller_name=self._extract_seller_name(soup, offers),
				seller_rating=self._extract_seller_rating(soup),
				availability=self._extract_availability(soup, offers),
				product_url=url,
				external_product_id=self.extract_external_product_id(url),
				scraped_at=datetime.now(timezone.utc),
				visible_sales_count=None,
			)
		except Exception as error:
			logger.exception("Parsing failed for %s: %s", url, error)
			self._set_failure("parse_failed", "The response could not be parsed into product data.")
			return None

		missing_critical_fields = [
			field_name
			for field_name in ("product_name", "current_price", "external_product_id")
			if getattr(parsed_data, field_name) is None
		]
		if missing_critical_fields:
			logger.warning(
				"Critical fields missing while parsing %s: %s",
				url,
				", ".join(missing_critical_fields),
			)
			if self.debug_parser and {"current_price", "product_name"}.intersection(missing_critical_fields):
				self._log_parser_debug_context(soup, structured_product, offers, url)
			self._set_failure(
				"invalid_data",
				f"Missing critical parsed fields: {', '.join(missing_critical_fields)}.",
			)
			return None

		missing_optional_fields = [
			field_name
			for field_name in (
				"brand",
				"model",
				"old_price",
				"discount_percentage",
				"seller_name",
				"seller_rating",
				"availability",
			)
			if getattr(parsed_data, field_name) is None
		]
		if missing_optional_fields:
			logger.info("Optional fields missing for %s: %s", url, ", ".join(missing_optional_fields))

		self._clear_failure_state()
		logger.info("Parsed %s product data successfully for %s", self.platform, url)
		return parsed_data

	def _extract_product_name(self, soup: BeautifulSoup, structured_product: dict[str, Any] | None) -> str | None:
		if structured_product:
			name = structured_product.get("name")
			if isinstance(name, str) and name.strip():
				return name.strip()

		return self._select_first_text(soup, self.PRODUCT_NAME_SELECTORS) or self._extract_meta_content(soup, "og:title")

	def _extract_brand(self, soup: BeautifulSoup, structured_product: dict[str, Any] | None) -> str | None:
		if structured_product:
			brand = structured_product.get("brand")
			if isinstance(brand, dict):
				brand_name = brand.get("name")
				if isinstance(brand_name, str) and brand_name.strip():
					return brand_name.strip()
			if isinstance(brand, str) and brand.strip():
				return brand.strip()

		return self._select_first_text(soup, self.BRAND_SELECTORS)

	def _extract_current_price(self, soup: BeautifulSoup, offers: dict[str, Any] | None) -> Decimal | None:
		if offers:
			price = self._extract_price_from_offer(offers)
			if price is not None:
				return price

		meta_price = self._extract_public_meta_price(soup)
		if meta_price is not None:
			return meta_price

		return self._extract_price_from_selectors(soup, self.CURRENT_PRICE_SELECTORS, skip_old_price_candidates=True)

	def _extract_old_price(self, soup: BeautifulSoup, offers: dict[str, Any] | None) -> Decimal | None:
		if offers:
			for key in ("highPrice", "priceBeforeDiscount"):
				price = self.normalize_money(self._extract_offer_value(offers, key))
				if price is not None:
					return price

		return self.normalize_money(self._select_first_text(soup, self.OLD_PRICE_SELECTORS))

	def _extract_discount_percentage(self, soup: BeautifulSoup) -> Decimal | None:
		return self.normalize_decimal(self._select_first_text(soup, self.DISCOUNT_SELECTORS))

	def _extract_currency(self, offers: dict[str, Any] | None) -> str | None:
		if not offers:
			return None

		currency = self._extract_offer_value(offers, "priceCurrency")
		if isinstance(currency, str) and currency.strip():
			return currency.strip().upper()

		return None

	def _extract_seller_name(self, soup: BeautifulSoup, offers: dict[str, Any] | None) -> str | None:
		if offers:
			seller = offers.get("seller")
			if isinstance(seller, dict):
				seller_name = seller.get("name")
				if isinstance(seller_name, str) and seller_name.strip():
					return seller_name.strip()
			if isinstance(seller, str) and seller.strip():
				return seller.strip()

		return self._select_first_text(soup, self.SELLER_NAME_SELECTORS)

	def _extract_seller_rating(self, soup: BeautifulSoup) -> Decimal | None:
		return self.normalize_decimal(self._select_first_text(soup, self.SELLER_RATING_SELECTORS))

	def _extract_availability(self, soup: BeautifulSoup, offers: dict[str, Any] | None) -> str | None:
		structured_value = self._normalize_availability(self._extract_offer_value(offers, "availability"))
		if structured_value is not None:
			return structured_value

		return self._normalize_availability(self._select_first_text(soup, self.AVAILABILITY_SELECTORS))

	def _extract_structured_product(self, soup: BeautifulSoup, url: str | None = None) -> dict[str, Any] | None:
		matching_candidates: list[tuple[int, dict[str, Any]]] = []
		fallback_candidates: list[tuple[int, dict[str, Any]]] = []
		preferred_types = {"product", "productgroup"}

		for script_tag in soup.select("script[type='application/ld+json']"):
			raw_payload = script_tag.string or script_tag.get_text(strip=True)
			if not raw_payload:
				continue

			try:
				payload = json.loads(raw_payload)
			except json.JSONDecodeError:
				continue

			for candidate in self._iter_json_ld_nodes(payload):
				types = self._normalize_json_ld_types(candidate.get("@type"))
				if not types.intersection(preferred_types):
					continue

				priority = 0 if "product" in types else 1
				fallback_candidates.append((priority, candidate))
				if url is not None and self._candidate_matches_url(candidate, url):
					matching_candidates.append((priority, candidate))

		if matching_candidates:
			matching_candidates.sort(key=lambda item: item[0])
			return matching_candidates[0][1]
		if fallback_candidates:
			fallback_candidates.sort(key=lambda item: item[0])
			return fallback_candidates[0][1]

		return None

	def _extract_offer_data(self, structured_product: dict[str, Any] | None, url: str | None = None) -> dict[str, Any] | None:
		if not structured_product:
			return None

		offers = structured_product.get("offers")
		if isinstance(offers, list):
			matching_offer = self._select_matching_offer(offers, url)
			if matching_offer is not None:
				return matching_offer
			return self._select_best_offer_candidate(offers)

		if isinstance(offers, dict):
			return offers

		return None

	def _extract_price_from_offer(self, offers: dict[str, Any] | None) -> Decimal | None:
		if not isinstance(offers, dict):
			return None

		for key in ("price", "lowPrice"):
			price = self.normalize_money(self._extract_offer_value(offers, key))
			if price is not None:
				return price

		price = self._extract_price_from_price_specification(offers.get("priceSpecification"))
		if price is not None:
			return price

		nested_offers = offers.get("offers")
		if isinstance(nested_offers, dict):
			return self._extract_price_from_offer(nested_offers)
		if isinstance(nested_offers, list):
			for candidate in nested_offers:
				price = self._extract_price_from_offer(candidate if isinstance(candidate, dict) else None)
				if price is not None:
					return price

		return None

	def _extract_price_from_price_specification(self, value: Any) -> Decimal | None:
		if isinstance(value, list):
			for candidate in value:
				price = self._extract_price_from_price_specification(candidate)
				if price is not None:
					return price
			return None

		if isinstance(value, dict):
			for key in ("price", "minPrice", "maxPrice"):
				price = self.normalize_money(value.get(key))
				if price is not None:
					return price
			return None

		return self.normalize_money(value)

	def _extract_public_meta_price(self, soup: BeautifulSoup) -> Decimal | None:
		for property_name in ("product:price:amount", "og:price:amount"):
			price = self.normalize_money(self._extract_meta_content(soup, property_name))
			if price is not None:
				return price

		for element in soup.select("meta[itemprop='price']"):
			value = element.get("content") if element.has_attr("content") else element.get_text(" ", strip=True)
			price = self.normalize_money(value)
			if price is not None:
				return price

		for element in soup.select("[itemprop='price']"):
			if self._is_old_price_element(element):
				continue
			value = element.get("content") if element.has_attr("content") else element.get_text(" ", strip=True)
			price = self.normalize_money(value)
			if price is not None:
				return price

		return None

	def _iter_json_ld_nodes(self, payload: Any) -> list[dict[str, Any]]:
		results: list[dict[str, Any]] = []
		if isinstance(payload, dict):
			results.append(payload)
			graph = payload.get("@graph")
			if isinstance(graph, list):
				for item in graph:
					if isinstance(item, dict):
						results.append(item)
		elif isinstance(payload, list):
			for item in payload:
				if isinstance(item, dict):
					results.extend(self._iter_json_ld_nodes(item))

		return results

	def _select_matching_offer(self, offers: list[Any], url: str | None) -> dict[str, Any] | None:
		for candidate in offers:
			if not isinstance(candidate, dict):
				continue
			if url is not None and self._candidate_matches_url(candidate, url):
				return candidate
		return None

	def _select_best_offer_candidate(self, offers: list[Any]) -> dict[str, Any] | None:
		scored_candidates: list[tuple[int, dict[str, Any]]] = []
		fallback_candidate: dict[str, Any] | None = None
		for candidate in offers:
			if not isinstance(candidate, dict):
				continue
			if fallback_candidate is None:
				fallback_candidate = candidate
			score = self._score_offer_candidate(candidate)
			if score is None:
				continue
			scored_candidates.append((score, candidate))

		if scored_candidates:
			scored_candidates.sort(key=lambda item: item[0])
			return scored_candidates[0][1]

		return fallback_candidate

	def _score_offer_candidate(self, candidate: dict[str, Any]) -> int | None:
		types = self._normalize_json_ld_types(candidate.get("@type"))
		has_price_signal = any(candidate.get(key) is not None for key in ("price", "lowPrice", "highPrice", "priceSpecification", "offers"))
		if not has_price_signal:
			return None
		if "offer" in types:
			return 0
		if "aggregateoffer" in types:
			return 1
		return 2

	@staticmethod
	def _normalize_json_ld_types(value: Any) -> set[str]:
		if isinstance(value, list):
			return {str(item).strip().lower() for item in value if str(item).strip()}
		if value is None:
			return set()
		text = str(value).strip()
		return {text.lower()} if text else set()

	def _candidate_matches_url(self, candidate: dict[str, Any], url: str) -> bool:
		reference_url = self._normalize_comparable_url(url)
		candidate_urls = [
			candidate.get("url"),
			candidate.get("@id"),
		]
		offers = candidate.get("offers")
		if isinstance(offers, dict):
			candidate_urls.append(offers.get("url"))

		for candidate_url in candidate_urls:
			if isinstance(candidate_url, str) and self._normalize_comparable_url(candidate_url) == reference_url:
				return True
		return False

	@staticmethod
	def _normalize_comparable_url(url: str) -> str:
		parsed_url = urlparse(url)
		path = parsed_url.path.rstrip("/") or "/"
		return f"{parsed_url.scheme.lower()}://{(parsed_url.netloc or '').lower()}{path}"

	@staticmethod
	def _extract_offer_value(offers: dict[str, Any] | None, key: str) -> Any:
		if offers is None:
			return None

		return offers.get(key)

	@staticmethod
	def _extract_meta_content(soup: BeautifulSoup, property_name: str) -> str | None:
		meta_tag = soup.find("meta", attrs={"property": property_name})
		if meta_tag is None:
			return None

		content = meta_tag.get("content")
		if isinstance(content, str) and content.strip():
			return content.strip()

		return None

	@staticmethod
	def _select_first_text(soup: BeautifulSoup, selectors: tuple[str, ...]) -> str | None:
		for selector in selectors:
			element = soup.select_one(selector)
			if element is None:
				continue

			text = element.get_text(" ", strip=True)
			if text:
				return text

		return None

	def _extract_price_from_selectors(
		self,
		soup: BeautifulSoup,
		selectors: tuple[str, ...],
		*,
		skip_old_price_candidates: bool,
	) -> Decimal | None:
		for selector in selectors:
			for element in soup.select(selector):
				if skip_old_price_candidates and self._is_old_price_element(element):
					continue
				text = element.get_text(" ", strip=True)
				if not text:
					continue
				price = self.normalize_money(text)
				if price is not None:
					return price
		return None

	def _is_old_price_element(self, element: Any) -> bool:
		markers = (
			"old-price",
			"price-old",
			"crossed",
			"strik",
			"original-price",
			"before-discount",
			"former-price",
		)
		for candidate in [element, *list(element.parents)]:
			if getattr(candidate, "name", None) in {"del", "s", "strike"}:
				return True
			attribute_values: list[str] = []
			for attribute_name in ("class", "id", "data-test-id", "data-testid"):
				attribute_value = candidate.get(attribute_name)
				if isinstance(attribute_value, list):
					attribute_values.extend(str(item).lower() for item in attribute_value)
				elif isinstance(attribute_value, str):
					attribute_values.append(attribute_value.lower())
			combined = " ".join(attribute_values)
			if any(marker in combined for marker in markers):
				return True
		return False

	@staticmethod
	def _normalize_availability(value: Any) -> str | None:
		if not isinstance(value, str) or not value.strip():
			return None

		normalized = value.strip().rsplit("/", maxsplit=1)[-1].replace("-", "_").replace(" ", "_").lower()
		mapping = {
			"instock": "in_stock",
			"in_stock": "in_stock",
			"outofstock": "out_of_stock",
			"out_of_stock": "out_of_stock",
			"preorder": "preorder",
		}
		return mapping.get(normalized, normalized)

	def _looks_like_block_page(self, soup: BeautifulSoup) -> bool:
		page_text = soup.get_text(" ", strip=True).lower()
		title_text = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
		indicators = (
			"access denied",
			"forbidden",
			"captcha",
			"robot verification",
			"verify you are human",
			"robot olmad",
			"güvenlik kontrol",
			"unusual traffic",
		)
		return any(indicator in page_text or indicator in title_text for indicator in indicators)

	def _is_platform_blocked(self, fetch_result: FetchResult) -> bool:
		if fetch_result.status_code in {403, 429}:
			return True
		soup = BeautifulSoup(fetch_result.html, "html.parser")
		return self._looks_like_block_page(soup)

	def _log_parser_debug_context(
		self,
		soup: BeautifulSoup,
		structured_product: dict[str, Any] | None,
		offers: dict[str, Any] | None,
		url: str,
	) -> None:
		logger.info("Parser debug for %s critical extraction on %s", self.platform, url)
		if structured_product is not None:
			logger.info(
				"Structured product candidate: type=%s name=%s",
				structured_product.get("@type"),
				structured_product.get("name"),
			)
		if offers is not None:
			logger.info(
				"Structured offer fields: type=%s price=%s lowPrice=%s highPrice=%s priceSpecification=%s",
				offers.get("@type"),
				offers.get("price"),
				offers.get("lowPrice"),
				offers.get("highPrice"),
				offers.get("priceSpecification"),
			)

		selector_matches = self._collect_debug_selector_matches(soup, self.CURRENT_PRICE_SELECTORS + self.OLD_PRICE_SELECTORS)
		if selector_matches:
			logger.info("Price selector matches: %s", " | ".join(selector_matches))

		meta_matches = self._collect_debug_meta_matches(soup)
		if meta_matches:
			logger.info("Price-related meta/itemprop matches: %s", " | ".join(meta_matches))

		name_matches = self._collect_debug_name_matches(soup)
		if name_matches:
			logger.info("Name selector matches: %s", " | ".join(name_matches))

	def _collect_debug_selector_matches(self, soup: BeautifulSoup, selectors: tuple[str, ...]) -> list[str]:
		matches: list[str] = []
		for selector in selectors:
			element = soup.select_one(selector)
			if element is None:
				continue
			text = element.get_text(" ", strip=True)
			if text:
				matches.append(f"{selector}={text[:120]}")
		return matches[:8]

	def _collect_debug_meta_matches(self, soup: BeautifulSoup) -> list[str]:
		results: list[str] = []
		for meta in soup.select("meta[property='product:price:amount'], meta[property='og:price:amount'], meta[itemprop='price'], [itemprop='price']"):
			if meta.name == "meta":
				name = meta.get("property") or meta.get("itemprop") or "meta"
				value = meta.get("content")
			else:
				name = meta.get("itemprop") or meta.name
				value = meta.get_text(" ", strip=True)
			if isinstance(value, str) and value.strip():
				results.append(f"{name}={value[:120]}")
		return results[:8]

	def _collect_debug_name_matches(self, soup: BeautifulSoup) -> list[str]:
		results = self._collect_debug_selector_matches(soup, self.PRODUCT_NAME_SELECTORS)
		og_title = self._extract_meta_content(soup, "og:title")
		if og_title:
			results.append(f"og:title={og_title[:120]}")
		for element in soup.select("meta[itemprop='name'], [itemprop='name']"):
			if element.name == "meta":
				value = element.get("content")
			else:
				value = element.get_text(" ", strip=True)
			if isinstance(value, str) and value.strip():
				results.append(f"itemprop:name={value[:120]}")
		return results[:8]

	def _set_failure(self, reason: str, details: str) -> None:
		self.last_failure_reason = reason
		self.last_failure_details = details

	def _clear_failure_state(self) -> None:
		self.last_failure_reason = None
		self.last_failure_details = None