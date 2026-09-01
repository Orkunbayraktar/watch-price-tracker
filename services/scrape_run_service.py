"""Read-only query helpers for scrape run operational visibility."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import selectinload

from database.db import db
from database.models import Listing, Product, ScrapeRun, ScrapeRunItem, Seller


@dataclass(slots=True)
class ScrapeRunSummary:
	"""Compact run summary used on dashboard and list views."""

	run_id: int
	platform: str
	status: str
	started_at: datetime
	finished_at: datetime | None
	duration: timedelta | None
	successful_items: int
	failed_items: int
	skipped_items: int
	total_items: int
	error_message: str | None


@dataclass(slots=True)
class ScrapeRunDetailItem:
	"""Detailed item-level row for a scrape run detail page."""

	item_id: int
	url: str
	platform: str | None
	status: str
	failure_reason: str | None
	error_message: str | None
	listing_id: int | None
	product_id: int | None
	product_name: str | None
	seller_name: str | None
	current_price: Decimal | None
	currency: str | None
	started_at: datetime
	finished_at: datetime | None


@dataclass(slots=True)
class ScrapeRunDetailData:
	"""Full payload for a scrape run detail page."""

	run: ScrapeRunSummary
	items: list[ScrapeRunDetailItem]


def get_latest_scrape_run_summary() -> ScrapeRunSummary | None:
	"""Return the latest scrape run summary, if any."""
	run = _base_run_query().first()
	if run is None:
		return None
	return _build_run_summary(run)


def get_recent_scrape_run_summaries(limit: int = 5) -> list[ScrapeRunSummary]:
	"""Return recent scrape run summaries ordered from newest to oldest."""
	runs = _base_run_query().limit(limit).all()
	return [_build_run_summary(run) for run in runs]


def get_scrape_run_detail(run_id: int) -> ScrapeRunDetailData | None:
	"""Return one scrape run and its item-level results."""
	run = _base_run_query().filter(ScrapeRun.id == run_id).first()
	if run is None:
		return None

	items = [
		ScrapeRunDetailItem(
			item_id=row.item_id,
			url=row.url,
			platform=row.platform,
			status=row.status,
			failure_reason=row.failure_reason,
			error_message=row.error_message,
			listing_id=row.listing_id,
			product_id=row.product_id,
			product_name=row.product_name,
			seller_name=row.seller_name,
			current_price=row.current_price,
			currency=row.currency,
			started_at=_ensure_utc(row.started_at),
			finished_at=_ensure_utc(row.finished_at) if row.finished_at is not None else None,
		)
		for row in (
			db.session.query(
				ScrapeRunItem.id.label("item_id"),
				ScrapeRunItem.url.label("url"),
				ScrapeRunItem.platform.label("platform"),
				ScrapeRunItem.status.label("status"),
				ScrapeRunItem.failure_reason.label("failure_reason"),
				ScrapeRunItem.error_message.label("error_message"),
				ScrapeRunItem.listing_id.label("listing_id"),
				Product.id.label("product_id"),
				Product.name.label("product_name"),
				Seller.name.label("seller_name"),
				Listing.current_price.label("current_price"),
				Listing.currency.label("currency"),
				ScrapeRunItem.started_at.label("started_at"),
				ScrapeRunItem.finished_at.label("finished_at"),
			)
			.outerjoin(Listing, ScrapeRunItem.listing_id == Listing.id)
			.outerjoin(Product, Listing.product_id == Product.id)
			.outerjoin(Seller, Listing.seller_id == Seller.id)
			.filter(ScrapeRunItem.scrape_run_id == run_id)
			.order_by(ScrapeRunItem.id.asc())
			.all()
		)
	]

	return ScrapeRunDetailData(run=_build_run_summary(run), items=items)


def _base_run_query():
	return (
		ScrapeRun.query.options(selectinload(ScrapeRun.items))
		.order_by(ScrapeRun.started_at.desc(), ScrapeRun.id.desc())
	)


def _build_run_summary(run: ScrapeRun) -> ScrapeRunSummary:
	started_at = _ensure_utc(run.started_at)
	finished_at = _ensure_utc(run.finished_at) if run.finished_at is not None else None
	total_items = len(run.items)
	skipped_items = sum(1 for item in run.items if item.status == "skipped")
	duration = None if finished_at is None else finished_at - started_at
	return ScrapeRunSummary(
		run_id=run.id,
		platform=run.platform,
		status=run.status,
		started_at=started_at,
		finished_at=finished_at,
		duration=duration,
		successful_items=run.products_found,
		failed_items=run.errors_count,
		skipped_items=skipped_items,
		total_items=total_items,
		error_message=run.error_message,
	)


def _ensure_utc(value: datetime) -> datetime:
	if value.tzinfo is None:
		return value.replace(tzinfo=timezone.utc)
	return value.astimezone(timezone.utc)