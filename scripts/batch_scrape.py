"""Manual CLI for controlled sequential batch scraping."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
	sys.path.insert(0, str(PROJECT_ROOT))


from app import create_app
from database.db import initialize_database
from services.batch_scraping_service import BatchScrapeItemResult, BatchScrapeResult, load_batch_urls, run_batch


def build_argument_parser() -> argparse.ArgumentParser:
	"""Create the CLI parser for the batch scraping command."""
	parser = argparse.ArgumentParser(description="Run a controlled sequential batch scrape from a text file of product URLs.")
	parser.add_argument("url_file", help="Path to a UTF-8 text file containing one product URL per line.")
	parser.add_argument("--platform", choices=("trendyol", "hepsiburada"), help="Optional explicit platform for all URLs in the file.")
	parser.add_argument("--fetcher", default="playwright", choices=("requests", "playwright"), help="Explicit fetch strategy for each URL.")
	parser.add_argument("--headed", action="store_true", help="Launch Playwright in headed mode for debugging.")
	parser.add_argument("--debug-parser", action="store_true", help="Log concise parser context when critical fields cannot be extracted.")
	return parser


def main() -> int:
	"""CLI entry point for sequential batch scraping."""
	logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
	args = build_argument_parser().parse_args()
	urls = load_batch_urls(args.url_file)

	app = create_app()
	initialize_database(app)

	with app.app_context():
		result = run_batch(
			urls,
			platform=args.platform,
			fetch_strategy=args.fetcher,
			headed=args.headed,
			debug_parser=args.debug_parser,
			on_run_started=lambda run: print_run_started(run.id, run.platform, args.fetcher, len(urls)),
			on_item_result=print_item_result,
		)

	print_run_finished(result)
	if result.status == "completed":
		return 0
	if result.status == "completed_with_errors":
		return 2
	return 1


def print_run_started(run_id: int, platform: str, fetcher: str, total_urls: int) -> None:
	"""Print the initial batch header once the run record exists."""
	print("Batch Scrape Started")
	print(f"Run ID: {run_id}")
	print(f"Platform: {platform}")
	print(f"Fetcher: {fetcher}")
	print(f"URLs: {total_urls}")
	print()


def print_item_result(index: int, total: int, item_result: BatchScrapeItemResult) -> None:
	"""Print one concise line for each finished batch item."""
	status = item_result.status.upper()
	if item_result.status == "success":
		price_text = f" - {item_result.current_price} {item_result.currency or 'TRY'}" if item_result.current_price is not None else ""
		print(f"[{index}/{total}] {status} - {item_result.product_name or item_result.url}{price_text}")
		return
	if item_result.failure_reason:
		print(f"[{index}/{total}] {status} - {item_result.failure_reason}")
		return
	print(f"[{index}/{total}] {status} - {item_result.url}")


def print_run_finished(result: BatchScrapeResult) -> None:
	"""Print the final batch summary."""
	print()
	print("Batch Scrape Completed")
	print(f"Status: {result.status}")
	print(f"Total: {result.total}")
	print(f"Successful: {result.successful}")
	print(f"Failed: {result.failed}")
	print(f"Skipped: {result.skipped}")
	print(f"Duration: {result.duration}")
	print(f"ScrapeRun ID: {result.run_id}")


if __name__ == "__main__":
	raise SystemExit(main())