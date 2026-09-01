"""Generic live data acquisition smoke test for one marketplace product URL."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
	sys.path.insert(0, str(PROJECT_ROOT))


from scrapers.hepsiburada_scraper import HepsiburadaScraper
from scrapers.trendyol_scraper import TrendyolScraper
from scripts.smoke_test_common import run_smoke_test


SCRAPER_BY_PLATFORM = {
	"trendyol": (TrendyolScraper, "Trendyol"),
	"hepsiburada": (HepsiburadaScraper, "Hepsiburada"),
}


def build_argument_parser() -> argparse.ArgumentParser:
	"""Create the CLI parser for the generic live smoke test."""
	parser = argparse.ArgumentParser(
		description="Run a controlled live smoke test for one public marketplace product URL.",
	)
	parser.add_argument("--platform", required=True, choices=sorted(SCRAPER_BY_PLATFORM))
	parser.add_argument("--fetcher", default="requests", choices=("requests", "playwright"))
	parser.add_argument("--headed", action="store_true", help="Launch Chromium in headed mode for debugging.")
	parser.add_argument("--debug-parser", action="store_true", help="Log concise price extraction debug context when current_price cannot be parsed.")
	parser.add_argument("url", help="A single supported marketplace product URL.")
	return parser


def main() -> int:
	"""CLI entry point for the generic live smoke test."""
	logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
	args = build_argument_parser().parse_args()
	scraper_class, platform_name = SCRAPER_BY_PLATFORM[args.platform]
	return run_smoke_test(
		args.url,
		scraper_class,
		platform_name,
		fetch_strategy=args.fetcher,
		headed=args.headed,
		debug_parser=args.debug_parser,
		label="Live smoke test",
	)


if __name__ == "__main__":
	raise SystemExit(main())