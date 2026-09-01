"""Development smoke test for a single Hepsiburada product URL."""

from __future__ import annotations

import logging
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
	sys.path.insert(0, str(PROJECT_ROOT))


from scrapers.hepsiburada_scraper import HepsiburadaScraper
from scripts.smoke_test_common import build_smoke_argument_parser, run_smoke_test


def main() -> int:
	"""CLI entry point for the Hepsiburada smoke test script."""
	logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
	args = build_smoke_argument_parser("Hepsiburada").parse_args()
	return run_smoke_test(args.url, HepsiburadaScraper, "Hepsiburada")


if __name__ == "__main__":
	raise SystemExit(main())