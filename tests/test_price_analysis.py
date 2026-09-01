"""Unit tests for price analysis helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app import create_app
from database.db import db, initialize_database
from database.models import Listing, PriceHistory, Product, Seller

from services.price_analysis_service import (
	PriceObservation,
	analyze_listing_observations,
	extract_price_change_events,
	get_dashboard_price_intelligence,
	get_product_price_intelligence_page,
	normalize_history_range,
)


def build_observation(
	history_id: int,
	price: Decimal | int | float | None,
	*,
	listing_id: int = 1,
	hours: int = 0,
) -> PriceObservation:
	return PriceObservation(
		history_id=history_id,
		listing_id=listing_id,
		price=None if price is None else Decimal(str(price)),
		recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=hours),
	)


def test_single_observation_has_no_previous_price() -> None:
	metrics = analyze_listing_observations([build_observation(1, "7499.90")])

	assert metrics.current_price == Decimal("7499.90")
	assert metrics.previous_distinct_price is None
	assert metrics.absolute_change is None
	assert metrics.percentage_change is None
	assert metrics.observation_count == 1
	assert metrics.actual_price_change_count == 0


def test_repeated_same_price_observations_do_not_count_as_price_changes() -> None:
	observations = [
		build_observation(1, "7000.00", hours=0),
		build_observation(2, "7000.00", hours=2),
		build_observation(3, "7000.00", hours=4),
	]

	metrics = analyze_listing_observations(observations)
	events = extract_price_change_events(observations)

	assert metrics.observation_count == 3
	assert metrics.actual_price_change_count == 0
	assert metrics.previous_distinct_price is None
	assert events == []


def test_price_decrease_uses_previous_distinct_price() -> None:
	observations = [
		build_observation(1, "7499.90", hours=0),
		build_observation(2, "7499.90", hours=2),
		build_observation(3, "6999.90", hours=4),
	]

	metrics = analyze_listing_observations(observations)
	events = extract_price_change_events(observations)

	assert metrics.current_price == Decimal("6999.90")
	assert metrics.previous_distinct_price == Decimal("7499.90")
	assert metrics.absolute_change == Decimal("-500.00")
	assert metrics.percentage_change == Decimal("-6.67")
	assert metrics.actual_price_change_count == 1
	assert len(events) == 1
	assert events[0].previous_price == Decimal("7499.90")
	assert events[0].current_price == Decimal("6999.90")


def test_price_increase_uses_previous_distinct_price() -> None:
	observations = [
		build_observation(1, "6999.90", hours=0),
		build_observation(2, "6999.90", hours=2),
		build_observation(3, "7299.90", hours=4),
	]

	metrics = analyze_listing_observations(observations)

	assert metrics.absolute_change == Decimal("300.00")
	assert metrics.percentage_change == Decimal("4.29")


def test_previous_distinct_price_skips_duplicate_tail_observations() -> None:
	metrics = analyze_listing_observations(
		[
			build_observation(1, "8000.00", hours=0),
			build_observation(2, "7600.00", hours=1),
			build_observation(3, "7600.00", hours=2),
			build_observation(4, "7600.00", hours=3),
		]
	)

	assert metrics.current_price == Decimal("7600.00")
	assert metrics.previous_distinct_price == Decimal("8000.00")


def test_min_max_and_average_use_all_valid_observations() -> None:
	metrics = analyze_listing_observations(
		[
			build_observation(1, "7000.00", hours=0),
			build_observation(2, "7300.00", hours=1),
			build_observation(3, "7600.00", hours=2),
		]
	)

	assert metrics.observed_min_price == Decimal("7000.00")
	assert metrics.observed_max_price == Decimal("7600.00")
	assert metrics.observed_average_price == Decimal("7300.00")


def test_first_price_and_change_since_first_are_reported() -> None:
	metrics = analyze_listing_observations(
		[
			build_observation(1, "7000.00", hours=0),
			build_observation(2, "7000.00", hours=1),
			build_observation(3, "7350.00", hours=2),
		]
	)

	assert metrics.first_observed_price == Decimal("7000.00")
	assert metrics.change_since_first == Decimal("350.00")
	assert metrics.change_since_first_percentage == Decimal("5.00")


def test_actual_change_count_uses_distinct_steps_only() -> None:
	metrics = analyze_listing_observations(
		[
			build_observation(1, "7000.00", hours=0),
			build_observation(2, "7000.00", hours=1),
			build_observation(3, "6800.00", hours=2),
			build_observation(4, "6800.00", hours=3),
			build_observation(5, "7100.00", hours=4),
		]
	)

	assert metrics.actual_price_change_count == 2


def test_percentage_change_uses_decimal_precision() -> None:
	metrics = analyze_listing_observations(
		[
			build_observation(1, "7499.90", hours=0),
			build_observation(2, "7180.30", hours=2),
		]
	)

	assert metrics.percentage_change == Decimal("-4.26")


def test_zero_previous_price_does_not_crash_percentage_calculation() -> None:
	metrics = analyze_listing_observations(
		[
			build_observation(1, "0.00", hours=0),
			build_observation(2, "100.00", hours=1),
		]
	)

	assert metrics.current_price == Decimal("100.00")
	assert metrics.first_observed_price == Decimal("100.00")
	assert metrics.percentage_change is None


def test_empty_history_returns_safe_empty_metrics() -> None:
	metrics = analyze_listing_observations([])

	assert metrics.current_price is None
	assert metrics.observation_count == 0
	assert metrics.actual_price_change_count == 0


def test_invalid_range_defaults_safely() -> None:
	assert normalize_history_range("unexpected") == "30d"
	assert normalize_history_range("7d") == "7d"
	assert normalize_history_range(None) == "30d"


@pytest.fixture
def analysis_app(tmp_path: Path):
	app = create_app(
		{
			"TESTING": True,
			"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
			"IMPORT_STATE_DIR": tmp_path / "import-state",
		}
	)
	app_context = app.app_context()
	app_context.push()
	initialize_database(app)
	yield app
	db.session.remove()
	db.drop_all()
	db.engine.dispose()
	app_context.pop()


def seed_analysis_data() -> dict[str, int]:
	now = datetime.now(timezone.utc)
	product = Product(brand="Casio", model="A159", name="Casio Retro")
	other_product = Product(brand="Seiko", model="SNK809", name="Seiko Field")
	seller_a = Seller(platform="trendyol", name="Market Time", rating=Decimal("4.70"))
	seller_b = Seller(platform="hepsiburada", name="Ocean Store", rating=Decimal("4.80"))
	seller_c = Seller(platform="imported", name="Static Seller", rating=None)
	db.session.add_all([product, other_product, seller_a, seller_b, seller_c])
	db.session.flush()

	listing_a = Listing(
		product=product,
		seller=seller_a,
		platform="trendyol",
		external_product_id="TR-ANALYSIS-1",
		url="https://example.com/tr-analysis-1",
		current_price=Decimal("6999.00"),
		currency="TRY",
		availability="in_stock",
		last_scraped_at=now - timedelta(hours=6),
	)
	listing_b = Listing(
		product=product,
		seller=seller_b,
		platform="hepsiburada",
		external_product_id="HB-ANALYSIS-1",
		url="https://example.com/hb-analysis-1",
		current_price=Decimal("7499.00"),
		currency="TRY",
		availability="in_stock",
		last_scraped_at=now - timedelta(hours=2),
	)
	listing_c = Listing(
		product=other_product,
		seller=seller_c,
		platform="imported",
		external_product_id="STATIC-1",
		url="import://static/1",
		current_price=Decimal("5000.00"),
		currency="TRY",
		availability="in_stock",
		last_scraped_at=now - timedelta(days=1),
	)
	db.session.add_all([listing_a, listing_b, listing_c])
	db.session.flush()

	db.session.add_all(
		[
			PriceHistory(listing=listing_a, price=Decimal("7600.00"), recorded_at=now - timedelta(days=40)),
			PriceHistory(listing=listing_a, price=Decimal("7200.00"), recorded_at=now - timedelta(days=20)),
			PriceHistory(listing=listing_a, price=Decimal("7200.00"), recorded_at=now - timedelta(days=6)),
			PriceHistory(listing=listing_a, price=Decimal("6999.00"), recorded_at=now - timedelta(days=1)),
			PriceHistory(listing=listing_b, price=Decimal("7300.00"), recorded_at=now - timedelta(days=5)),
			PriceHistory(listing=listing_b, price=Decimal("7300.00"), recorded_at=now - timedelta(days=2)),
			PriceHistory(listing=listing_b, price=Decimal("7499.00"), recorded_at=now - timedelta(hours=3)),
			PriceHistory(listing=listing_c, price=Decimal("5000.00"), recorded_at=now - timedelta(days=4)),
			PriceHistory(listing=listing_c, price=Decimal("5000.00"), recorded_at=now - timedelta(days=1)),
		]
	)
	db.session.commit()
	return {
		"product_id": product.id,
		"listing_a_id": listing_a.id,
		"listing_b_id": listing_b.id,
		"other_product_id": other_product.id,
	}


def test_product_metrics_choose_cheapest_listing_and_compute_spread(analysis_app) -> None:
	ids = seed_analysis_data()
	page = get_product_price_intelligence_page(ids["product_id"], range_key="all")

	assert page is not None
	assert page.metrics.cheapest_current_listing_id == ids["listing_a_id"]
	assert page.metrics.current_min_price == Decimal("6999.00")
	assert page.metrics.current_max_price == Decimal("7499.00")
	assert page.metrics.current_average_price == Decimal("7249.00")
	assert page.metrics.price_spread_amount == Decimal("500.00")
	assert page.metrics.price_spread_percentage == Decimal("7.14")


def test_product_metrics_include_multiple_sellers_and_active_listing_count(analysis_app) -> None:
	ids = seed_analysis_data()
	page = get_product_price_intelligence_page(ids["product_id"], range_key="all")

	assert page is not None
	assert page.metrics.seller_count == 2
	assert page.metrics.active_listing_count == 2
	assert len(page.seller_comparison) == 2
	assert page.seller_comparison[0].is_cheapest is True
	assert page.seller_comparison[1].difference_from_cheapest == Decimal("500.00")
	assert page.seller_comparison[1].difference_percentage == Decimal("7.14")


def test_product_range_7d_filters_selected_listing_history(analysis_app) -> None:
	ids = seed_analysis_data()
	page = get_product_price_intelligence_page(ids["product_id"], range_key="7d")

	assert page is not None
	assert page.selected_range == "7d"
	assert page.selected_listing_id == ids["listing_a_id"]
	assert page.history_total_count == 2
	assert page.chart.point_count == 2


def test_product_range_30d_filters_selected_listing_history(analysis_app) -> None:
	ids = seed_analysis_data()
	page = get_product_price_intelligence_page(ids["product_id"], range_key="30d")

	assert page is not None
	assert page.selected_range == "30d"
	assert page.history_total_count == 3
	assert page.chart.point_count == 3


def test_product_range_all_keeps_full_selected_listing_history(analysis_app) -> None:
	ids = seed_analysis_data()
	page = get_product_price_intelligence_page(ids["product_id"], range_key="all")

	assert page is not None
	assert page.history_total_count == 4
	assert page.chart.point_count == 4


def test_dashboard_biggest_drops_exclude_unchanged_observations(analysis_app) -> None:
	seed_analysis_data()
	intelligence = get_dashboard_price_intelligence()

	assert len(intelligence.biggest_drops) == 1
	assert intelligence.biggest_drops[0].product_name == "Casio Retro"
	assert intelligence.biggest_drops[0].old_price == Decimal("7200.00")
	assert intelligence.biggest_drops[0].new_price == Decimal("6999.00")
	assert all(item.product_name != "Seiko Field" for item in intelligence.biggest_drops)


def test_dashboard_biggest_increases_return_positive_moves(analysis_app) -> None:
	seed_analysis_data()
	intelligence = get_dashboard_price_intelligence()

	assert len(intelligence.biggest_increases) == 1
	assert intelligence.biggest_increases[0].old_price == Decimal("7300.00")
	assert intelligence.biggest_increases[0].new_price == Decimal("7499.00")
	assert intelligence.biggest_increases[0].percentage_change == Decimal("2.73")


def test_recent_changes_exclude_repeated_identical_observations(analysis_app) -> None:
	seed_analysis_data()
	intelligence = get_dashboard_price_intelligence()

	assert len(intelligence.recent_changes) == 2
	assert {item.new_price for item in intelligence.recent_changes} == {Decimal("6999.00"), Decimal("7499.00")}
	assert all(item.product_name != "Seiko Field" for item in intelligence.recent_changes)


def test_chart_data_contains_expected_timestamps_and_prices(analysis_app) -> None:
	ids = seed_analysis_data()
	page = get_product_price_intelligence_page(ids["product_id"], range_key="all")

	assert page is not None
	assert [point.price for point in page.chart.points] == [
		Decimal("7600.00"),
		Decimal("7200.00"),
		Decimal("7200.00"),
		Decimal("6999.00"),
	]
	assert page.chart.points[0].recorded_at_label
	assert page.chart.points[-1].recorded_at_label


def test_invalid_range_on_product_page_defaults_to_30d(analysis_app) -> None:
	ids = seed_analysis_data()
	page = get_product_price_intelligence_page(ids["product_id"], range_key="unexpected")

	assert page is not None
	assert page.selected_range == "30d"
	assert page.history_total_count == 3