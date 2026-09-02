"""Application orchestration for safe brand discovery and Watchlist import."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from database.models import WatchlistItem
from discovery import (
	BaseDiscoveryProvider,
	DiscoveredProduct,
	DiscoveryResult,
	HepsiburadaDiscoveryProvider,
	TrendyolDiscoveryProvider,
)
from services.url_service import normalize_tracking_url
from services.watchlist_service import WatchlistValidationError, create_watchlist_item, validate_and_normalize_watchlist_url


ProviderFactory = Callable[[], BaseDiscoveryProvider]
DEFAULT_PROVIDER_FACTORIES: dict[str, ProviderFactory] = {
	"trendyol": TrendyolDiscoveryProvider,
	"hepsiburada": HepsiburadaDiscoveryProvider,
}


@dataclass(frozen=True, slots=True)
class DiscoveryAddResult:
	"""Summary of an explicit selected-product Watchlist import."""

	added_item_ids: tuple[int, ...] = ()
	already_tracked: int = 0
	skipped: int = 0

	@property
	def added_count(self) -> int:
		return len(self.added_item_ids)


def discover_products(
	platform: str,
	brand: str,
	*,
	max_products: int,
	max_pages: int,
	absolute_max_products: int = 200,
	provider: BaseDiscoveryProvider | None = None,
	provider_factories: dict[str, ProviderFactory] | None = None,
) -> DiscoveryResult:
	"""Validate bounded input, select a provider, and deduplicate its result."""
	normalized_platform = (platform or "").strip().lower()
	normalized_brand = " ".join((brand or "").split())
	if not normalized_brand or len(normalized_brand) > 120 or any(ord(character) < 32 for character in normalized_brand):
		return _failure(normalized_platform, normalized_brand, "invalid_brand", "Enter a brand name between 1 and 120 characters.")

	factories = provider_factories or DEFAULT_PROVIDER_FACTORIES
	if provider is None:
		factory = factories.get(normalized_platform)
		if factory is None:
			return _failure(normalized_platform, normalized_brand, "unsupported_platform", "Select a supported marketplace.")
		provider = factory()
	elif provider.platform != normalized_platform:
		return _failure(normalized_platform, normalized_brand, "unsupported_platform", "The selected provider does not match the marketplace.")

	bounded_products = max(1, min(_as_int(max_products, 1), max(1, absolute_max_products)))
	bounded_pages = max(1, _as_int(max_pages, 1))
	result = provider.discover(normalized_brand, max_pages=bounded_pages, max_products=bounded_products)
	return _deduplicate_result(result, bounded_products)


def get_tracked_discovery_urls(products: tuple[DiscoveredProduct, ...]) -> set[str]:
	"""Return preview URLs already present in the Watchlist."""
	urls = [normalize_tracking_url(product.product_url) for product in products]
	if not urls:
		return set()
	return {row.url for row in WatchlistItem.query.filter(WatchlistItem.url.in_(urls)).all()}


def add_discovered_products(result: DiscoveryResult, selected_urls: list[str]) -> DiscoveryAddResult:
	"""Add only selected preview URLs through the existing Watchlist service."""
	if not selected_urls:
		return DiscoveryAddResult()

	preview_products = {normalize_tracking_url(product.product_url): product for product in result.products}
	added_ids: list[int] = []
	already_tracked = 0
	skipped = 0
	seen: set[str] = set()

	for raw_url in selected_urls:
		try:
			url = validate_and_normalize_watchlist_url(raw_url)
		except WatchlistValidationError:
			skipped += 1
			continue
		if url in seen:
			continue
		seen.add(url)
		product = preview_products.get(url)
		if product is None:
			skipped += 1
			continue
		try:
			item = create_watchlist_item(url, display_name=product.product_name)
		except WatchlistValidationError as error:
			if "already being tracked" in str(error):
				already_tracked += 1
			else:
				skipped += 1
		else:
			added_ids.append(item.id)

	return DiscoveryAddResult(tuple(added_ids), already_tracked, skipped)


def _deduplicate_result(result: DiscoveryResult, limit: int) -> DiscoveryResult:
	products_by_url: dict[str, DiscoveredProduct] = {}
	for product in result.products:
		try:
			canonical_url = validate_and_normalize_watchlist_url(product.product_url)
		except WatchlistValidationError:
			continue
		if canonical_url in products_by_url:
			continue
		products_by_url[canonical_url] = DiscoveredProduct(
			platform=product.platform,
			product_url=canonical_url,
			external_product_id=product.external_product_id,
			product_name=product.product_name,
			brand=product.brand,
			current_price=product.current_price,
			currency=product.currency,
			image_url=product.image_url,
			discovery_source_url=product.discovery_source_url,
		)
		if len(products_by_url) >= limit:
			break
	return DiscoveryResult(
		platform=result.platform,
		brand=result.brand,
		status=result.status,
		source_url=result.source_url,
		products=tuple(products_by_url.values()),
		pages_scanned=result.pages_scanned,
		failure_reason=result.failure_reason,
		message=result.message,
	)


def _failure(platform: str, brand: str, reason: str, message: str) -> DiscoveryResult:
	return DiscoveryResult(
		platform=platform,
		brand=brand,
		status="failed",
		source_url=None,
		failure_reason=reason,
		message=message,
	)


def _as_int(value: object, fallback: int) -> int:
	try:
		return int(value)
	except (TypeError, ValueError):
		return fallback
