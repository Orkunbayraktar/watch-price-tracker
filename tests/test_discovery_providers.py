"""Tests for robots-aware marketplace discovery providers."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from discovery import BaseDiscoveryProvider, HepsiburadaDiscoveryProvider, TrendyolDiscoveryProvider
from scrapers.fetchers import FetchResult, FetcherError
from services.product_discovery_service import discover_products


TRENDYOL_HTML = """
<main>
  <div class="p-card-wrppr">
    <a href="/casio/f-91w-dijital-saat-p-1001?boutiqueId=61" title="Casio F-91W Dijital Saat">
      <span class="prdct-desc-cntnr-ttl">Casio</span>
      <span class="prdct-desc-cntnr-name">F-91W Dijital Saat</span>
      <span class="prc-box-dscntd">1.249,90 TL</span>
    </a>
  </div>
  <div class="p-card-wrppr">
    <a href="https://www.trendyol.com/casio/f-91w-dijital-saat-p-1001#reviews">Duplicate</a>
  </div>
  <div class="p-card-wrppr">
    <a href="/casio/ga-2100-kol-saati-p-1002" title="Casio GA-2100 Kol Saati">
      <span class="prc-box-sllng">7.499 TL</span>
    </a>
  </div>
  <a href="/sr?q=Casio">Not a product</a>
  <a href="https://evil.example/watch-p-999">Unsafe host</a>
</main>
"""


class FakeFetcher:
	def __init__(self, results: list[FetchResult] | None = None, error: FetcherError | None = None) -> None:
		self.results = list(results or [])
		self.error = error
		self.calls: list[str] = []

	def fetch(self, url: str, **_kwargs) -> FetchResult:
		self.calls.append(url)
		if self.error is not None:
			raise self.error
		return self.results.pop(0)


def allowed_robots() -> Mock:
	manager = Mock()
	parser = Mock()
	parser.can_fetch.return_value = True
	manager.load_rules.return_value = parser
	manager.get_crawl_delay.return_value = 0
	return manager


def provider_with(*results: FetchResult) -> TrendyolDiscoveryProvider:
	return TrendyolDiscoveryProvider(
		robots_manager=allowed_robots(),
		fetcher=FakeFetcher(list(results)),
		default_request_delay=0,
	)


def test_trendyol_provider_accepts_valid_brand_and_controls_host() -> None:
	provider = provider_with(FetchResult(TRENDYOL_HTML, "https://www.trendyol.com/sr?q=Casio&pi=1", 200))

	result = discover_products("trendyol", "  Casio  ", max_products=20, max_pages=1, provider=provider)
	query = parse_qs(urlsplit(result.source_url or "").query)

	assert result.status == "success"
	assert result.brand == "Casio"
	assert urlsplit(result.source_url or "").hostname == "www.trendyol.com"
	assert query == {"q": ["Casio"], "pi": ["1"]}


def test_unsafe_brand_input_stays_inside_encoded_query_on_trendyol_host() -> None:
	provider = TrendyolDiscoveryProvider()
	url = provider.build_discovery_url("Casio&next=https://evil.example", 1)

	assert urlsplit(url).hostname == "www.trendyol.com"
	assert parse_qs(urlsplit(url).query)["q"] == ["Casio&next=https://evil.example"]


def test_robots_denial_prevents_fetch() -> None:
	manager = allowed_robots()
	manager.load_rules.return_value.can_fetch.return_value = False
	fetcher = FakeFetcher([FetchResult(TRENDYOL_HTML, "https://www.trendyol.com/sr", 200)])
	provider = TrendyolDiscoveryProvider(robots_manager=manager, fetcher=fetcher, default_request_delay=0)

	result = provider.discover("Casio", max_pages=1, max_products=20)

	assert result.failure_reason == "robots_denied"
	assert fetcher.calls == []


def test_trendyol_page_extracts_deduplicated_urls_ids_and_decimal_price() -> None:
	provider = provider_with(FetchResult(TRENDYOL_HTML, "https://www.trendyol.com/sr?q=Casio&pi=1", 200))

	result = provider.discover("Casio", max_pages=1, max_products=20)

	assert result.discovered_count == 2
	assert result.products[0].product_url == "https://www.trendyol.com/casio/f-91w-dijital-saat-p-1001"
	assert result.products[0].external_product_id == "1001"
	assert result.products[0].product_name == "Casio F-91W Dijital Saat"
	assert result.products[0].current_price == Decimal("1249.90")
	assert result.products[0].currency == "TRY"


def test_product_and_page_limits_are_enforced_sequentially() -> None:
	fetcher = FakeFetcher([
		FetchResult(TRENDYOL_HTML, "https://www.trendyol.com/sr?q=Casio&pi=1", 200),
		FetchResult(TRENDYOL_HTML.replace("1001", "2001").replace("1002", "2002"), "https://www.trendyol.com/sr?q=Casio&pi=2", 200),
	])
	provider = TrendyolDiscoveryProvider(robots_manager=allowed_robots(), fetcher=fetcher, default_request_delay=0)

	result = provider.discover("Casio", max_pages=2, max_products=3)

	assert result.pages_scanned == 2
	assert result.discovered_count == 3
	assert len(fetcher.calls) == 2


def test_max_products_stops_before_another_page_fetch() -> None:
	fetcher = FakeFetcher([FetchResult(TRENDYOL_HTML, "https://www.trendyol.com/sr?q=Casio&pi=1", 200)])
	provider = TrendyolDiscoveryProvider(robots_manager=allowed_robots(), fetcher=fetcher, default_request_delay=0)

	result = provider.discover("Casio", max_pages=3, max_products=1)

	assert result.discovered_count == 1
	assert len(fetcher.calls) == 1


def test_hepsiburada_implements_common_interface_and_maps_blocking() -> None:
	fetcher = FakeFetcher([FetchResult("<h1>Forbidden</h1>", "https://www.hepsiburada.com/ara?q=Casio", 403)])
	provider = HepsiburadaDiscoveryProvider(robots_manager=allowed_robots(), fetcher=fetcher, default_request_delay=0)

	result = provider.discover("Casio", max_pages=1, max_products=20)

	assert isinstance(provider, BaseDiscoveryProvider)
	assert urlsplit(provider.build_discovery_url("Casio", 1)).hostname == "www.hepsiburada.com"
	assert result.status == "failed"
	assert result.failure_reason == "blocked_by_platform"


def test_fetch_timeout_is_structured() -> None:
	provider = TrendyolDiscoveryProvider(
		robots_manager=allowed_robots(),
		fetcher=FakeFetcher(error=FetcherError("browser_timeout", "timeout")),
		default_request_delay=0,
	)

	result = provider.discover("Casio", max_pages=1, max_products=20)

	assert result.failure_reason == "timeout"


def test_blank_brand_and_unsupported_platform_are_structured_failures() -> None:
	blank = discover_products("trendyol", "   ", max_products=20, max_pages=1)
	unsupported = discover_products("unknown", "Casio", max_products=20, max_pages=1)

	assert blank.failure_reason == "invalid_brand"
	assert unsupported.failure_reason == "unsupported_platform"
