"""Offline catalog aggregation, safety, limits, and public-card parsing tests."""

from dataclasses import replace
from decimal import Decimal
from unittest.mock import Mock
from urllib.robotparser import RobotFileParser

import pytest
import requests

from app import create_app  # Initialize the existing app/config import order.
from discovery import saatvesaat
from discovery.models import DiscoveryResult
from discovery.saatvesaat import CATALOG_TARGETS, CatalogFetcher, CatalogTarget, SaatVeSaatDiscoveryProvider
from scrapers.fetchers import FetchResult, FetcherError
from services.product_discovery_service import discover_products


ROOT = "https://www.saatvesaat.com.tr"


def card(code, *, slug="watch", href=None):
    """Minimal fixture matching the inspected server-rendered catalog markup."""
    url = href or f"/{slug}-p-{code}"
    return f'''<div class="product-item initial-product" data-product-id="99999">
      <a class="product-item-photo" href="{url}"><img src="/images/watch.jpg"></a>
      <a class="brand" href="{url}">Fossil</a>
      <a class="name" href="{url}">{code} Kol Saati</a>
      <span class="special-price" data-price-type="specialPrice" data-price-amount="4930.2">4.930,20 TL</span>
      <span class="price">4.680,20 TL Sepette indirim</span>
    </div>'''


def catalog_html(*codes):
    return '<div class="products-grid">' + ''.join(card(code) for code in codes) + '</div>'


class OfflineFetcher:
    def __init__(self, overrides=None, per_catalog=12):
        self.overrides = overrides or {}
        self.per_catalog = per_catalog
        self.calls = []

    def fetch(self, url, **kwargs):
        assert url.startswith(ROOT + '/') and '?' not in url and '#' not in url
        assert kwargs['user_agent'] == 'WatchPriceTracker/1.0'
        self.calls.append(url)
        outcome = self.overrides.get(url)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is not None:
            return outcome
        index = next(i for i, target in enumerate(saatvesaat.CATALOG_TARGETS) if ROOT + target.path == url)
        return FetchResult(catalog_html(*(f'SKU{index}-{i}' for i in range(self.per_catalog))), url, 200)


def make_provider(*, denied=(), overrides=None, per_catalog=12):
    rules = RobotFileParser()
    rules.parse(['User-agent: *', *[f'Disallow: {path}' for path in denied]])
    robots = Mock()
    robots.load_rules.return_value = rules
    robots.get_crawl_delay.return_value = 0
    fetcher = OfflineFetcher(overrides, per_catalog)
    provider = SaatVeSaatDiscoveryProvider(robots_manager=robots, fetcher=fetcher, default_request_delay=0)
    return provider, fetcher, robots


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Offline discovery tests attempted an outbound HTTP request')
    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)


@pytest.mark.parametrize(('target', 'catalogs'), [(100, 9), (200, 17)])
def test_target_aggregation_stops_immediately(target, catalogs):
    provider, fetcher, robots = make_provider()
    result = provider.discover_catalogs(target_products=target, max_catalogs=25)
    assert result.status == 'success'
    assert result.discovered_count == target
    assert result.stop_reason == 'target_reached'
    assert len(fetcher.calls) == result.catalogs_scanned == catalogs
    assert robots.load_rules.call_count == catalogs
    assert len({product.external_product_id for product in result.products}) == target
    assert all(product.external_product_id != '99999' for product in result.products)
    assert result.catalog_outcomes[-1].products_added == target % 12
    assert DiscoveryResult.from_dict(result.to_dict()) == result


def test_defaults_are_100_products_and_15_catalogs():
    provider, fetcher, _ = make_provider(per_catalog=1)
    result = provider.discover_catalogs()
    assert result.target_products == 100
    assert result.catalog_limit == 15
    assert len(fetcher.calls) == 15
    assert result.discovered_count == 15
    assert result.status == 'partial'
    assert result.stop_reason == 'catalog_limit'


def test_maximum_25_catalogs_even_if_pool_grows(monkeypatch):
    monkeypatch.setattr(saatvesaat, 'CATALOG_TARGETS', CATALOG_TARGETS + (CatalogTarget('Test extra', '/test-extra'),))
    provider, fetcher, _ = make_provider(per_catalog=1)
    result = provider.discover_catalogs(target_products=200, max_catalogs=25)
    assert result.catalogs_requested == 26
    assert len(fetcher.calls) == result.catalogs_scanned == 25
    assert result.stop_reason == 'catalog_limit'
    assert ROOT + '/test-extra' not in fetcher.calls


@pytest.mark.parametrize('value', [None, '', 'abc', '100.5', 100.5, True, False, 0, -1, 24, 201, 500])
def test_invalid_product_target_never_fetches(value):
    provider, fetcher, robots = make_provider()
    result = provider.discover_catalogs(target_products=value)
    assert result.failure_reason == 'invalid_limits'
    assert fetcher.calls == []
    robots.load_rules.assert_not_called()


@pytest.mark.parametrize('value', [None, '', 'abc', '1.5', 1.5, True, 0, -1, 26, 100])
def test_invalid_catalog_limit_never_fetches(value):
    provider, fetcher, _ = make_provider()
    assert provider.discover_catalogs(max_catalogs=value).failure_reason == 'invalid_limits'
    assert fetcher.calls == []


def test_duplicate_product_codes_across_catalogs_and_equivalent_urls():
    first = ROOT + '/fossil'
    second = ROOT + '/seiko'
    html = '<div class="products-grid">' + card('sku0-0', slug='changed-name') + card('NEW') + '</div>'
    provider, fetcher, _ = make_provider(overrides={second: FetchResult(html, second, 200)})
    result = provider.discover_catalogs(['fossil', 'seiko', 'fossil'], target_products=25)
    assert fetcher.calls == [first, second]
    assert result.discovered_count == 13
    assert result.duplicates_skipped == 1
    assert result.catalogs_requested == 2
    assert result.products[0].external_product_id == 'SKU0-0'


def test_failed_catalog_preserves_products_and_continues():
    second = ROOT + '/seiko'
    provider, fetcher, _ = make_provider(overrides={second: FetcherError('navigation_error', 'offline failure')})
    result = provider.discover_catalogs(['fossil', 'seiko', 'timex'], target_products=25)
    assert len(fetcher.calls) == 3
    assert result.status == 'partial'
    assert result.discovered_count == 24
    assert result.catalogs_scanned == 2
    assert result.catalog_failures == 1
    assert result.catalog_outcomes[1].reason == 'navigation_error'


def test_robots_disallowed_catalog_is_skipped_without_fetch():
    provider, fetcher, robots = make_provider(denied=['/seiko'])
    result = provider.discover_catalogs(['fossil', 'seiko', 'timex'], target_products=25)
    assert fetcher.calls == [ROOT + '/fossil', ROOT + '/timex']
    assert robots.load_rules.call_count == 3
    assert result.discovered_count == 24
    assert result.catalog_failures == 1
    assert result.catalog_outcomes[1].reason == 'robots_disallowed'


def test_robots_load_failure_stops_without_fetching():
    provider, fetcher, robots = make_provider()
    robots.load_rules.return_value = None
    result = provider.discover_catalogs()
    assert result.failure_reason == 'robots_load_failed'
    assert fetcher.calls == []


@pytest.mark.parametrize('blocked', [
    FetchResult('Forbidden', ROOT + '/seiko', 403),
    FetchResult('Rate limited', ROOT + '/seiko', 429),
    FetchResult('<title>CAPTCHA</title>', ROOT + '/seiko', 200),
])
def test_platform_block_stops_run_and_keeps_partial_results(blocked):
    provider, fetcher, _ = make_provider(overrides={ROOT + '/seiko': blocked})
    result = provider.discover_catalogs(target_products=100)
    assert len(fetcher.calls) == 2
    assert result.status == 'partial'
    assert result.discovered_count == 12
    assert result.stop_reason == 'blocked_by_platform'


@pytest.mark.parametrize(('status', 'final_url'), [(302, ROOT + '/seiko'), (200, 'https://evil.example/'), (200, ROOT + '/seiko?p=2')])
def test_unexpected_redirect_is_not_parsed(status, final_url):
    provider, fetcher, _ = make_provider(overrides={ROOT + '/seiko': FetchResult(catalog_html('REDIRECT'), final_url, status)})
    result = provider.discover_catalogs(['fossil', 'seiko', 'timex'], target_products=25)
    assert result.discovered_count == 24
    assert result.catalog_outcomes[1].reason == 'navigation_error'
    assert all(product.external_product_id != 'REDIRECT' for product in result.products)
    assert len(fetcher.calls) == 3


def test_http_fetcher_never_follows_redirects(monkeypatch):
    get = Mock(return_value=Mock(text='', url=ROOT + '/fossil', status_code=302))
    monkeypatch.setattr(saatvesaat.requests, 'get', get)
    result = CatalogFetcher().fetch(ROOT + '/fossil', user_agent='WatchPriceTracker/1.0', timeout_seconds=20)
    assert result.status_code == 302
    assert get.call_args.kwargs['allow_redirects'] is False


def test_catalog_pool_exhaustion_returns_usable_partial_result():
    provider, fetcher, _ = make_provider()
    result = provider.discover_catalogs(['fossil'], target_products=100)
    assert result.discovered_count == 12
    assert result.status == 'partial'
    assert result.stop_reason == 'catalog_pool_exhausted'
    assert result.catalog_failures == 0


def test_empty_or_changed_markup_is_a_failure_not_a_product():
    provider, _, _ = make_provider(overrides={ROOT + '/fossil': FetchResult('<main>Empty</main>', ROOT + '/fossil', 200)})
    result = provider.discover_catalogs(['fossil', 'seiko'], target_products=25)
    assert result.discovered_count == 12
    assert result.catalog_outcomes[0].reason == 'parsing_error'


@pytest.mark.parametrize('ids', [[], ['unknown'], ['https://evil.example'], ['fossil?p=2'], ['../fossil']])
def test_unknown_user_catalog_targets_are_rejected(ids):
    provider, fetcher, _ = make_provider()
    assert provider.discover_catalogs(ids).failure_reason == 'invalid_catalogs'
    assert fetcher.calls == []


@pytest.mark.parametrize('path', ['//evil.example', 'https://evil.example', '/fossil?p=2', '/fossil#x', '/../fossil', '/%66ossil', '/foo\\bar'])
def test_catalog_definition_rejects_unsafe_paths(path):
    with pytest.raises(ValueError):
        CatalogTarget('Unsafe', path)


def test_card_metadata_uses_url_code_and_catalog_price_not_cart_offer():
    provider, _, _ = make_provider()
    product = provider.parse_discovery_page(catalog_html('fle1247set'), ROOT + '/fossil')[0]
    assert product.external_product_id == 'FLE1247SET'
    assert product.current_price == Decimal('4930.20')
    assert product.brand == 'Fossil'
    assert product.product_name == 'Fossil fle1247set Kol Saati'
    assert product.image_url == ROOT + '/images/watch.jpg'
    assert product.currency == 'TRY'


@pytest.mark.parametrize('href', ['https://evil.example/watch-p-SKU', 'https://user@www.saatvesaat.com.tr/watch-p-SKU', 'https://www.saatvesaat.com.tr:9000/watch-p-SKU', 'javascript:alert(1)'])
def test_unsafe_product_card_urls_are_discarded(href):
    provider, _, _ = make_provider()
    assert provider.parse_discovery_page('<div class="products-grid">' + card('SKU', href=href) + '</div>', ROOT + '/fossil') == []


def test_service_uses_injected_provider_factory():
    provider, fetcher, _ = make_provider()
    factory = Mock(return_value=provider)
    result = discover_products('saatvesaat', '', max_products=100, max_pages=3,
                               provider_factories={'saatvesaat': factory}, max_catalogs=15)
    factory.assert_called_once_with()
    assert result.discovered_count == 100
    assert len(fetcher.calls) == 9


def test_hepsiburada_discovery_is_disabled_before_factory_creation():
    factory = Mock()
    result = discover_products('hepsiburada', 'Casio', max_products=100, max_pages=3,
                               provider_factories={'hepsiburada': factory})
    assert result.failure_reason == 'blocked_by_platform'
    factory.assert_not_called()
