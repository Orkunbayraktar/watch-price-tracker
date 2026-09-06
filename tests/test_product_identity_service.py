"""Canonical product identity shared by discovery and Watchlist."""

import pytest

from app import create_app
from services.product_identity_service import canonical_product_url, product_identity


@pytest.mark.parametrize('url', [
    'https://www.saatvesaat.com.tr/fossil-watch-p-fle1247set',
    'http://saatvesaat.com.tr/another-slug-p-FLE1247SET/?utm_source=test#details',
])
def test_saatvesaat_identity_is_case_normalized_url_code(url):
    assert product_identity(url) == ('saatvesaat', 'FLE1247SET')


@pytest.mark.parametrize('url', [
    'https://www.trendyol.com/casio/watch-p-123?boutiqueId=61',
    'http://trendyol.com/new-slug-p-123/#reviews',
])
def test_trendyol_identity_ignores_slug_host_and_query_variants(url):
    assert product_identity(url) == ('trendyol', '123')


def test_product_identity_is_namespaced_by_platform():
    assert product_identity('https://www.trendyol.com/watch-p-123') != product_identity('https://www.saatvesaat.com.tr/watch-p-123')


def test_canonical_product_url_normalizes_only_known_id_bearing_urls():
    assert canonical_product_url('http://saatvesaat.com.tr/watch-p-sku1/?utm_source=test#reviews') == 'https://www.saatvesaat.com.tr/watch-p-sku1'
    assert canonical_product_url('https://example.com/search?q=watch#results') == 'https://example.com/search?q=watch'


@pytest.mark.parametrize('url', ['https://user@www.saatvesaat.com.tr/watch-p-sku1', 'https://www.saatvesaat.com.tr:9999/watch-p-sku1'])
def test_canonicalization_rejects_unsafe_authority(url):
    with pytest.raises(ValueError):
        canonical_product_url(url)
