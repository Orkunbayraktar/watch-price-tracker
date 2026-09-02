"""Run a read-only marketplace brand discovery smoke test."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
	sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import Config
from services.product_discovery_service import discover_products


def main() -> int:
	parser = argparse.ArgumentParser(description="Discover public marketplace products by brand without persistence.")
	parser.add_argument("--platform", required=True, choices=("trendyol", "hepsiburada"))
	parser.add_argument("--brand", required=True)
	parser.add_argument("--max-products", type=int, default=20)
	parser.add_argument("--max-pages", type=int, default=Config.DISCOVERY_MAX_PAGES)
	args = parser.parse_args()

	result = discover_products(
		args.platform,
		args.brand,
		max_products=args.max_products,
		max_pages=min(args.max_pages, Config.DISCOVERY_MAX_PAGES),
		absolute_max_products=Config.DISCOVERY_ABSOLUTE_MAX_PRODUCTS,
	)
	if result.status != "success":
		print("Discovery failed")
		print(f"\nPlatform: {result.platform}")
		print(f"Brand: {result.brand}")
		print(f"Reason: {result.failure_reason}")
		return 1

	print("Discovery successful")
	print(f"\nPlatform: {result.platform}")
	print(f"Brand: {result.brand}")
	print(f"Pages scanned: {result.pages_scanned}")
	print(f"Products discovered: {result.discovered_count}\n")
	for index, product in enumerate(result.products[:5], start=1):
		print(f"{index}. {product.product_name or product.product_url}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
