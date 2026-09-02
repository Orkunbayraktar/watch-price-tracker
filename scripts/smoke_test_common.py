"""Shared helpers for manual marketplace smoke tests."""

from __future__ import annotations

import argparse
import logging

from app import create_app
from database.db import db, initialize_database
from database.models import Listing, PriceHistory, Product, Seller
from scrapers.models import ScrapedProductData
from scrapers.product_page_scraper import ProductPageScraper
from services.persistence_service import PersistenceError, save_scraped_product
from services.scraping_service import scrape_product


logger = logging.getLogger(__name__)


def build_smoke_argument_parser(platform_name: str) -> argparse.ArgumentParser:
	"""Create a CLI parser for a marketplace smoke test."""
	parser = argparse.ArgumentParser(
		description=f"Run a development smoke test for a single {platform_name} product URL.",
	)
	parser.add_argument(
		"--debug-parser",
		action="store_true",
		help="Log concise structured-data and price-selector context when critical parser fields are missing.",
	)
	parser.add_argument("url", help=f"The {platform_name} product URL to scrape and persist.")
	return parser


def run_smoke_test(
	url: str,
	scraper_class: type[ProductPageScraper],
	platform_name: str,
	*,
	fetch_strategy: str = "requests",
	headed: bool = False,
	debug_parser: bool = False,
	label: str = "Smoke test",
) -> int:
	"""Execute the scraper-to-database smoke test for one product URL."""
	platform = scraper_class.platform
	if not scraper_class.is_supported_url(url):
		_print_failure(
			"unsupported_url",
			f"Provide a supported {platform_name} product URL.",
			label=label,
			platform=platform,
			fetcher_name=fetch_strategy,
		)
		return 2

	app = create_app()
	initialize_database(app)
	scraper = scraper_class(debug_parser=debug_parser)

	with app.app_context():
		scraped_data = scrape_product(url, scraper=scraper, fetch_strategy=fetch_strategy, headed=headed)
		if scraped_data is None:
			return report_scrape_failure(
				scraper,
				label=label,
				platform=platform,
				fetcher_name=fetch_strategy,
			)

		missing_fields = get_missing_critical_fields(scraped_data)
		if missing_fields:
			_print_failure(
				"invalid_data",
				f"Critical scraped fields are missing: {', '.join(missing_fields)}",
				label=label,
				platform=platform,
				fetcher_name=fetch_strategy,
			)
			return 3

		try:
			result = save_scraped_product(scraped_data)
		except PersistenceError as error:
			_print_failure(
				"persistence_failed",
				str(error),
				label=label,
				platform=platform,
				fetcher_name=fetch_strategy,
			)
			return 4

		if result is None:
			_print_failure(
				"invalid_data",
				"Persistence validation rejected the scraped product data.",
				label=label,
				platform=platform,
				fetcher_name=fetch_strategy,
			)
			return 5

		verified_listing = verify_saved_listing(result.listing.id, scraped_data)
		if verified_listing is None:
			_print_failure(
				"persistence_verification_failed",
				"Database verification failed after persistence.",
				label=label,
				platform=platform,
				fetcher_name=fetch_strategy,
			)
			return 6

		print_success_summary(
			scraped_data,
			verified_listing,
			label=label,
			fetcher_name=fetch_strategy,
		)
		return 0


def verify_saved_listing(listing_id: int, scraped_data: ScrapedProductData) -> Listing | None:
	"""Query the saved listing again and confirm the critical persisted values."""
	listing = db.session.get(Listing, listing_id)
	if listing is None:
		return None
	if listing.product is None:
		return None
	if listing.current_price != scraped_data.current_price:
		return None
	if listing.external_product_id != scraped_data.external_product_id:
		return None
	if listing.price_history is None or len(listing.price_history) < 1:
		return None
	if scraped_data.seller_name is not None and listing.seller is None:
		return None
	return listing


def print_success_summary(
	scraped_data: ScrapedProductData,
	listing: Listing,
	*,
	label: str = "Smoke test",
	fetcher_name: str | None = None,
) -> None:
	"""Print a readable summary for a successful smoke test run."""
	matching_listing_count = Listing.query.filter(
		Listing.platform == scraped_data.platform,
		Listing.external_product_id == scraped_data.external_product_id,
		Listing.url == scraped_data.product_url,
	).count()
	price_history_count = PriceHistory.query.filter_by(listing_id=listing.id).count()

	print(f"{label} successful")
	print()
	print(f"Platform: {scraped_data.platform}")
	if fetcher_name is not None:
		print(f"Fetcher: {fetcher_name}")
	print(f"Product: {display_value(scraped_data.product_name)}")
	print(f"Brand: {display_value(scraped_data.brand)}")
	print(f"Model: {display_value(scraped_data.model)}")
	print(f"Seller: {display_value(listing.seller.name if listing.seller else None)}")
	print(f"Current Price: {display_value(scraped_data.current_price)} {display_value(scraped_data.currency)}")
	print(f"Old Price: {display_value(scraped_data.old_price)}")
	print(f"Discount: {display_value(scraped_data.discount_percentage)}")
	print(f"Availability: {display_value(scraped_data.availability)}")
	print(f"External Product ID: {display_value(scraped_data.external_product_id)}")
	print(f"Listing ID: {listing.id}")
	print(f"Price history count: {price_history_count}")
	print(f"Matched Listings For This URL: {matching_listing_count}")
	print(f"Scraped At: {scraped_data.scraped_at.isoformat()}")
	print()
	print("Database totals:")
	print(f"Products: {Product.query.count()}")
	print(f"Sellers: {Seller.query.count()}")
	print(f"Listings: {Listing.query.count()}")
	print(f"Price history records: {PriceHistory.query.count()}")


def report_scrape_failure(
	scraper: ProductPageScraper,
	*,
	label: str = "Smoke test",
	platform: str | None = None,
	fetcher_name: str | None = None,
) -> int:
	"""Print a readable failure message based on the scraper's last failure state."""
	reason = scraper.last_failure_reason or "scrape_failed"
	details = scraper.last_failure_details or "The scraper could not produce valid product data."
	_print_failure(reason, details, label=label, platform=platform, fetcher_name=fetcher_name)
	return 1


def get_missing_critical_fields(scraped_data: ScrapedProductData) -> list[str]:
	"""Return any critical scraped fields that are missing from the DTO."""
	missing_fields: list[str] = []
	for field_name in (
		"platform",
		"product_name",
		"current_price",
		"external_product_id",
		"product_url",
		"scraped_at",
	):
		value = getattr(scraped_data, field_name)
		if value is None:
			missing_fields.append(field_name)
		elif isinstance(value, str) and not value.strip():
			missing_fields.append(field_name)

	return missing_fields


def display_value(value: object) -> str:
	"""Format optional values for terminal output."""
	if value is None:
		return "Not available"
	return str(value)


def _print_failure(
	reason: str,
	details: str,
	*,
	label: str = "Smoke test",
	platform: str | None = None,
	fetcher_name: str | None = None,
) -> None:
	message_map = {
		"unsupported_url": "Unsupported URL.",
		"robots_denied": "robots.txt denied access.",
		"robots_load_failed": "robots.txt could not be loaded.",
		"http_forbidden": "HTTP 403 response received.",
		"rate_limited": "HTTP 429 response received.",
		"timeout": "The request timed out.",
		"browser_launch_failed": "The browser could not be launched.",
		"browser_navigation_failed": "The browser could not finish navigation.",
		"browser_timeout": "The browser timed out.",
		"blocked_by_platform": "The platform blocked browser automation.",
		"challenge_detected": "A challenge or access-denied page was detected.",
		"invalid_fetch_strategy": "The selected fetch strategy is not supported.",
		"parse_failed": "The response could not be parsed into product data.",
		"invalid_data": "Required product fields could not be extracted.",
		"http_error": "An HTTP error occurred.",
		"request_failed": "The request failed before a valid page was returned.",
		"persistence_failed": "Persistence failed.",
		"persistence_verification_failed": "Persistence verification failed.",
		"scrape_failed": "Scraping failed.",
	}
	print(f"{label} failed")
	if platform is not None:
		print(f"Platform: {platform}")
	if fetcher_name is not None:
		print(f"Fetcher: {fetcher_name}")
	print(f"Reason: {reason}")
	print(f"Message: {message_map.get(reason, 'Scraping failed.')}")
	print(f"Details: {details}")
