"""Single-product Trendyol scraping proof of concept."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from requests import Session

from config.settings import Config
from scrapers.base_scraper import BaseScraper
from scrapers.models import ScrapedProductData


logger = logging.getLogger(__name__)


class TrendyolScraper(BaseScraper):
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
	MODEL_PATTERN = re.compile(r"\b[A-Z]{1,6}(?:-[A-Z0-9]{1,10}){1,4}\b")

	def __init__(
		self,
		robots_manager=None,
		user_agent: str | None = None,
		default_request_delay: int | float | None = None,
		session: Session | None = None,
	) -> None:
		super().__init__(
			robots_manager=robots_manager,
			user_agent=user_agent,
			default_request_delay=default_request_delay,
		)
		self.session = session or requests.Session()
		self.session.headers.setdefault("User-Agent", self.user_agent)

	@classmethod
	def is_supported_url(cls, url: str) -> bool:
		"""Return whether the URL belongs to a supported Trendyol product host."""
		parsed_url = urlparse(url)
		return parsed_url.scheme in {"http", "https"} and (parsed_url.hostname or "").lower() in cls.ALLOWED_HOSTS

	@classmethod
	def extract_external_product_id(cls, url: str) -> str | None:
		"""Extract the Trendyol external product ID from the URL path."""
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

	def scrape_product(self, url: str) -> ScrapedProductData | None:
		"""Fetch and parse a single Trendyol product page."""
		if not self.is_supported_url(url):
			logger.warning("Unsupported Trendyol URL: %s", url)
			return None

		logger.info("Starting Trendyol scrape for %s", url)
		html = self.fetch_product_page(url)
		if html is None:
			return None

		return self.parse_product_page(html, url)

	def fetch_product_page(self, url: str) -> str | None:
		"""Fetch a Trendyol product page after robots.txt approval."""
		if not self.is_supported_url(url):
			logger.warning("Rejected non-Trendyol URL: %s", url)
			return None

		if not self.is_allowed(url):
			logger.warning("Robots.txt denied access to %s", url)
			return None

		self.wait_for_request_slot(url)
		self.mark_request_started()

		try:
			response = self.session.get(url, timeout=Config.REQUEST_TIMEOUT)
			response.raise_for_status()
		except requests.HTTPError as error:
			logger.warning("HTTP error while fetching %s: %s", url, error)
			return None
		except requests.RequestException as error:
			logger.warning("Request failed for %s: %s", url, error)
			return None

		logger.info("Fetched Trendyol product page successfully: %s", url)
		return response.text

	def parse_product_page(self, html: str, url: str) -> ScrapedProductData | None:
		"""Parse a Trendyol product page into normalized Python data."""
		soup = BeautifulSoup(html, "html.parser")
		structured_product = self._extract_structured_product(soup)
		offers = self._extract_offer_data(structured_product)

		product_name = self._extract_product_name(soup, structured_product)
		brand = self._extract_brand(soup, structured_product)
		parsed_data = ScrapedProductData(
			platform=self.platform,
			product_name=product_name,
			brand=brand,
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

		missing_critical_fields = [
			field_name
			for field_name in (
				"product_name",
				"current_price",
				"external_product_id",
			)
			if getattr(parsed_data, field_name) is None
		]
		if missing_critical_fields:
			logger.warning(
				"Critical fields missing while parsing %s: %s",
				url,
				", ".join(missing_critical_fields),
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

		logger.info("Parsed Trendyol product data successfully for %s", url)
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
			price = self.normalize_money(self._extract_offer_value(offers, "price"))
			if price is not None:
				return price

		return self.normalize_money(self._select_first_text(soup, self.CURRENT_PRICE_SELECTORS))

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

	def _extract_structured_product(self, soup: BeautifulSoup) -> dict[str, Any] | None:
		for script_tag in soup.select("script[type='application/ld+json']"):
			raw_payload = script_tag.string or script_tag.get_text(strip=True)
			if not raw_payload:
				continue

			try:
				payload = json.loads(raw_payload)
			except json.JSONDecodeError:
				continue

			for candidate in self._iter_json_ld_nodes(payload):
				type_value = candidate.get("@type")
				types = type_value if isinstance(type_value, list) else [type_value]
				if any(item == "Product" for item in types):
					return candidate

		return None

	def _extract_offer_data(self, structured_product: dict[str, Any] | None) -> dict[str, Any] | None:
		if not structured_product:
			return None

		offers = structured_product.get("offers")
		if isinstance(offers, list):
			for candidate in offers:
				if isinstance(candidate, dict):
					return candidate
			return None

		if isinstance(offers, dict):
			return offers

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
