"""Query helpers for the read-only analytics dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func

from database.db import db
from database.models import Listing, PriceHistory, Product, Seller
from services.price_analysis_service import DashboardPriceIntelligence, get_dashboard_price_intelligence
from services.scrape_run_service import ScrapeRunSummary, get_latest_scrape_run_summary, get_recent_scrape_run_summaries


@dataclass(slots=True)
class DashboardMetrics:
	"""Top-level dashboard counters."""

	total_products: int
	total_sellers: int
	active_listings: int
	price_observations: int


@dataclass(slots=True)
class RecentPriceObservationItem:
	"""Recent price observation row for the dashboard."""

	product_id: int
	product_name: str
	platform: str
	seller_name: str | None
	price: Decimal
	currency: str
	recorded_at: datetime


@dataclass(slots=True)
class RecentListingItem:
	"""Recently updated listing row for the dashboard."""

	product_id: int
	product_name: str
	platform: str
	seller_name: str | None
	current_price: Decimal
	currency: str
	last_updated: datetime | None


@dataclass(slots=True)
class MarketplaceBreakdownItem:
	"""Platform-level listing count for simple marketplace comparisons."""

	platform: str
	listing_count: int
	share_percent: int


@dataclass(slots=True)
class DashboardData:
	"""Complete dashboard payload for the homepage."""

	metrics: DashboardMetrics
	recent_price_observations: list[RecentPriceObservationItem]
	recent_listings: list[RecentListingItem]
	marketplace_breakdown: list[MarketplaceBreakdownItem]
	price_intelligence: DashboardPriceIntelligence
	latest_scrape_run: ScrapeRunSummary | None
	recent_scrape_runs: list[ScrapeRunSummary]


def get_dashboard_data(observation_limit: int = 8, listing_limit: int = 8) -> DashboardData:
	"""Return real dashboard metrics and recent activity from the database."""
	metrics = DashboardMetrics(
		total_products=Product.query.count(),
		total_sellers=Seller.query.count(),
		active_listings=Listing.query.count(),
		price_observations=PriceHistory.query.count(),
	)

	recent_observations = [
		RecentPriceObservationItem(
			product_id=row.product_id,
			product_name=row.product_name,
			platform=row.platform,
			seller_name=row.seller_name,
			price=row.price,
			currency=row.currency,
			recorded_at=row.recorded_at,
		)
		for row in (
			db.session.query(
				Product.id.label("product_id"),
				Product.name.label("product_name"),
				Listing.platform.label("platform"),
				Seller.name.label("seller_name"),
				PriceHistory.price.label("price"),
				Listing.currency.label("currency"),
				PriceHistory.recorded_at.label("recorded_at"),
			)
			.join(Listing, PriceHistory.listing_id == Listing.id)
			.join(Product, Listing.product_id == Product.id)
			.outerjoin(Seller, Listing.seller_id == Seller.id)
			.order_by(PriceHistory.recorded_at.desc(), PriceHistory.id.desc())
			.limit(observation_limit)
			.all()
		)
	]

	last_updated_expr = func.coalesce(Listing.last_scraped_at, Listing.updated_at, Listing.created_at)
	recent_listings = [
		RecentListingItem(
			product_id=row.product_id,
			product_name=row.product_name,
			platform=row.platform,
			seller_name=row.seller_name,
			current_price=row.current_price,
			currency=row.currency,
			last_updated=row.last_updated,
		)
		for row in (
			db.session.query(
				Product.id.label("product_id"),
				Product.name.label("product_name"),
				Listing.platform.label("platform"),
				Seller.name.label("seller_name"),
				Listing.current_price.label("current_price"),
				Listing.currency.label("currency"),
				last_updated_expr.label("last_updated"),
			)
			.join(Product, Listing.product_id == Product.id)
			.outerjoin(Seller, Listing.seller_id == Seller.id)
			.order_by(last_updated_expr.desc(), Listing.id.desc())
			.limit(listing_limit)
			.all()
		)
	]

	breakdown_rows = (
		db.session.query(
			Listing.platform.label("platform"),
			func.count(Listing.id).label("listing_count"),
		)
		.group_by(Listing.platform)
		.order_by(func.count(Listing.id).desc(), Listing.platform.asc())
		.all()
	)
	max_count = max((row.listing_count for row in breakdown_rows), default=0)
	marketplace_breakdown = [
		MarketplaceBreakdownItem(
			platform=row.platform,
			listing_count=row.listing_count,
			share_percent=0 if max_count == 0 else round((row.listing_count / max_count) * 100),
		)
		for row in breakdown_rows
	]

	return DashboardData(
		metrics=metrics,
		recent_price_observations=recent_observations,
		recent_listings=recent_listings,
		marketplace_breakdown=marketplace_breakdown,
		price_intelligence=get_dashboard_price_intelligence(),
		latest_scrape_run=get_latest_scrape_run_summary(),
		recent_scrape_runs=get_recent_scrape_run_summaries(),
	)