"""Transactional, explicit operations for clearing local application data."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Callable

from sqlalchemy import or_

from database.db import db
from database.models import Listing, PriceHistory, Product, ScrapeRun, ScrapeRunItem, ScrapeSchedule, Seller, WatchlistItem
from services.scrape_run_service import get_running_scrape_run_summary


logger = logging.getLogger(__name__)


class DataManagementError(RuntimeError):
	"""Base error for data-management operations."""


class DataManagementBlockedError(DataManagementError):
	"""Raised when an active scrape makes deletion unsafe."""


class DataManagementConfirmationError(DataManagementError):
	"""Raised when Reset All is missing its exact typed confirmation."""


@dataclass(frozen=True, slots=True)
class DataManagementResult:
	success: bool
	operation: str
	deleted_counts: dict[str, int]
	message: str


@dataclass(frozen=True, slots=True)
class DataInventory:
	products: int
	sellers: int
	listings: int
	price_history: int
	watchlist_items: int
	scrape_runs: int
	scrape_run_items: int
	running_run_id: int | None


def get_data_inventory() -> DataInventory:
	"""Return real record counts for confirmation context on Settings."""
	running_run = get_running_scrape_run_summary()
	return DataInventory(
		products=Product.query.count(),
		sellers=Seller.query.count(),
		listings=Listing.query.count(),
		price_history=PriceHistory.query.count(),
		watchlist_items=WatchlistItem.query.count(),
		scrape_runs=ScrapeRun.query.count(),
		scrape_run_items=ScrapeRunItem.query.count(),
		running_run_id=running_run.run_id if running_run is not None else None,
	)


def clear_price_history() -> DataManagementResult:
	"""Delete observations while preserving listings and their current prices."""
	return _execute(
		"clear_price_history",
		lambda: {"price_history": _delete_all(PriceHistory)},
		lambda counts: f"Price history cleared: {counts['price_history']} price record(s) removed. Listings, current prices, and Watchlist preserved.",
	)


def clear_scrape_history() -> DataManagementResult:
	"""Delete item-level and batch scraping history only."""
	def delete_records() -> dict[str, int]:
		ScrapeSchedule.query.filter(ScrapeSchedule.last_scrape_run_id.is_not(None)).update(
			{ScrapeSchedule.last_scrape_run_id: None},
			synchronize_session=False,
		)
		return {
			"scrape_run_items": _delete_all(ScrapeRunItem),
			"scrape_runs": _delete_all(ScrapeRun),
		}

	return _execute(
		"clear_scrape_history",
		delete_records,
		lambda counts: f"Scrape history cleared: {counts['scrape_runs']} run(s) and {counts['scrape_run_items']} item result(s) removed. Marketplace data and Watchlist preserved.",
	)


def clear_marketplace_data() -> DataManagementResult:
	"""Delete collected marketplace data while preserving monitoring configuration."""
	def delete_records() -> dict[str, int]:
		ScrapeRunItem.query.filter(ScrapeRunItem.listing_id.is_not(None)).update(
			{ScrapeRunItem.listing_id: None},
			synchronize_session=False,
		)
		metadata_reset = WatchlistItem.query.filter(
			or_(
				WatchlistItem.last_scraped_at.is_not(None),
				WatchlistItem.last_scrape_status.is_not(None),
				WatchlistItem.last_failure_reason.is_not(None),
			)
		).update(
			{
				WatchlistItem.last_scraped_at: None,
				WatchlistItem.last_scrape_status: None,
				WatchlistItem.last_failure_reason: None,
			},
			synchronize_session=False,
		)
		return {
			"price_history": _delete_all(PriceHistory),
			"listings": _delete_all(Listing),
			"sellers": _delete_all(Seller),
			"products": _delete_all(Product),
			"watchlist_metadata_reset": metadata_reset,
		}

	return _execute(
		"clear_marketplace_data",
		delete_records,
		lambda counts: (
			f"Marketplace data cleared: {counts['products']} product(s), {counts['sellers']} seller(s), "
			f"{counts['listings']} listing(s), and {counts['price_history']} price record(s) removed. "
			f"Watchlist preserved; scrape metadata reset for {counts['watchlist_metadata_reset']} item(s)."
		),
	)


def clear_watchlist() -> DataManagementResult:
	"""Delete monitoring configuration without deleting marketplace history."""
	return _execute(
		"clear_watchlist",
		lambda: {"watchlist_items": _delete_all(WatchlistItem)},
		lambda counts: f"Watchlist cleared: {counts['watchlist_items']} tracked URL(s) removed. Products, listings, and price history preserved.",
	)


def reset_all_data(confirmation: str) -> DataManagementResult:
	"""Clear business and operational records while retaining schema and settings."""
	if confirmation != "RESET":
		raise DataManagementConfirmationError("Type RESET exactly to confirm a full data reset.")

	def delete_records() -> dict[str, int]:
		ScrapeSchedule.query.filter(ScrapeSchedule.last_scrape_run_id.is_not(None)).update(
			{ScrapeSchedule.last_scrape_run_id: None},
			synchronize_session=False,
		)
		return {
			"scrape_run_items": _delete_all(ScrapeRunItem),
			"scrape_runs": _delete_all(ScrapeRun),
			"price_history": _delete_all(PriceHistory),
			"listings": _delete_all(Listing),
			"sellers": _delete_all(Seller),
			"products": _delete_all(Product),
			"watchlist_items": _delete_all(WatchlistItem),
		}

	return _execute(
		"reset_all_data",
		delete_records,
		lambda counts: (
			"All local business and operational data reset: "
			f"{counts['products']} product(s), {counts['sellers']} seller(s), {counts['listings']} listing(s), "
			f"{counts['price_history']} price record(s), {counts['watchlist_items']} Watchlist item(s), "
			f"{counts['scrape_runs']} run(s), and {counts['scrape_run_items']} run item(s) removed. "
			"Database schema and application settings preserved."
		),
	)


def _execute(
	operation: str,
	delete_records: Callable[[], dict[str, int]],
	build_message: Callable[[dict[str, int]], str],
) -> DataManagementResult:
	try:
		running_run = get_running_scrape_run_summary()
		if running_run is not None:
			raise DataManagementBlockedError(
				f"Data cannot be cleared while scraping run {running_run.run_id} is running."
			)
		deleted_counts = delete_records()
		message = build_message(deleted_counts)
		db.session.commit()
	except (DataManagementBlockedError, DataManagementConfirmationError):
		db.session.rollback()
		raise
	except Exception as error:
		db.session.rollback()
		logger.exception("Data management operation failed: %s", operation)
		raise DataManagementError("The data operation failed and all changes were rolled back.") from error

	logger.info("%s completed with counts: %s", operation, deleted_counts)
	return DataManagementResult(True, operation, deleted_counts, message)


def _delete_all(model) -> int:
	return model.query.delete(synchronize_session=False)
