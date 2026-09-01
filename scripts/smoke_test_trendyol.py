"""Development smoke test for a single Trendyol product URL."""

from __future__ import annotations

import logging
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
	sys.path.insert(0, str(PROJECT_ROOT))


from scripts.smoke_test_common import build_smoke_argument_parser, run_smoke_test
from scrapers.trendyol_scraper import TrendyolScraper
logger = logging.getLogger(__name__)


def main() -> int:
	"""CLI entry point for the Trendyol smoke test script."""
	logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
	args = build_smoke_argument_parser("Trendyol").parse_args()
	return run_smoke_test(args.url, TrendyolScraper, "Trendyol")


if __name__ == "__main__":
	raise SystemExit(main())