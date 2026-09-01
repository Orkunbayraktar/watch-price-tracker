"""Route and rendering tests for the Data Quality monitoring UI."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from bs4 import BeautifulSoup

from app import create_app
from database.db import db, initialize_database
from database.models import Listing, Product, ScrapeRun, ScrapeRunItem, WatchlistItem
from services.batch_scraping_service import BatchScrapeItemResult, BatchScrapeResult


NOW = datetime.now(timezone.utc)


@pytest.fixture
def client(tmp_path: Path):
	app = create_app(
		{
			"TESTING": True,
			"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
			"IMPORT_STATE_DIR": tmp_path / "import-state",
			"DATA_QUALITY_STALE_HOURS": 48,
			"DATA_QUALITY_PAGE_SIZE": 25,
		}
	)
	with app.app_context():
		initialize_database(app)
		with app.test_client() as test_client:
			yield test_client
		db.session.remove()
		db.drop_all()
		db.engine.dispose()


def add_item(
	name: str,
	*,
	index: int = 1,
	platform: str = "trendyol",
	is_active: bool = True,
) -> WatchlistItem:
	item = WatchlistItem(
		platform=platform,
		url=f"https://www.{platform}.com/quality-watch-{index}-p-{1000 + index}",
		external_product_id=str(1000 + index),
		display_name=name,
		is_active=is_active,
	)
	db.session.add(item)
	db.session.commit()
	return item


def add_attempt(
	item: WatchlistItem,
	*,
	status: str,
	reason: str | None = None,
	at: datetime = NOW,
) -> ScrapeRunItem:
	run = ScrapeRun(
		platform=item.platform,
		started_at=at,
		finished_at=at + timedelta(minutes=1),
		status="completed" if status == "success" else "failed",
		products_found=1 if status == "success" else 0,
		listings_found=1 if status == "success" else 0,
		errors_count=0 if status == "success" else 1,
	)
	attempt = ScrapeRunItem(
		scrape_run=run,
		url=item.url,
		platform=item.platform,
		status=status,
		failure_reason=reason,
		started_at=at,
		finished_at=at + timedelta(minutes=1),
	)
	db.session.add_all([run, attempt])
	db.session.commit()
	return attempt


def test_data_quality_page_renders_summary_and_empty_state(client) -> None:
	response = client.get("/data-quality")
	body = response.get_data(as_text=True)

	assert response.status_code == 200
	assert "Data Quality" in body
	assert "Healthy" in body
	assert "No tracked items match these filters." in body


def test_filters_search_and_sort_are_applied_server_side(client) -> None:
	first = add_item("Zulu blocked", index=1)
	add_attempt(first, status="failed", reason="blocked_by_platform")
	second = add_item("Alpha blocked", index=2)
	add_attempt(second, status="failed", reason="http_forbidden")
	add_item("Unrelated paused", index=3, platform="hepsiburada", is_active=False)

	response = client.get("/data-quality?status=blocked&platform=trendyol&tracking=active&search=blocked&sort=product")
	body = response.get_data(as_text=True)

	assert response.status_code == 200
	assert "Alpha blocked" in body
	assert "Zulu blocked" in body
	assert body.index("Alpha blocked") < body.index("Zulu blocked")
	assert "Unrelated paused" not in body


def test_pagination_preserves_filter_query_parameters(client) -> None:
	for index in range(26):
		item = add_item(f"Blocked {index:02d}", index=index + 1)
		add_attempt(item, status="failed", reason="blocked_by_platform")

	response = client.get("/data-quality?status=blocked&platform=trendyol&tracking=active&sort=product&page=1")
	soup = BeautifulSoup(response.get_data(as_text=True), "html.parser")
	next_link = soup.find("a", string="Next")

	assert next_link is not None
	query = parse_qs(urlparse(unescape(next_link["href"])).query)
	assert query["status"] == ["blocked"]
	assert query["platform"] == ["trendyol"]
	assert query["tracking"] == ["active"]
	assert query["sort"] == ["product"]
	assert query["page"] == ["2"]


def test_latest_known_price_and_neutral_no_data_price_render(client) -> None:
	priced = add_item("Priced watch", index=10)
	product = Product(name="Casio Priced Watch")
	listing = Listing(
		product=product,
		platform="trendyol",
		external_product_id=priced.external_product_id,
		url=priced.url,
		current_price=Decimal("1234.50"),
		currency="TRY",
		last_scraped_at=NOW,
	)
	db.session.add_all([product, listing])
	db.session.commit()
	add_item("No data watch", index=11)

	body = client.get("/data-quality").get_data(as_text=True)

	assert "1.234,50 TL" in body
	assert "No price data" in body
	assert "0,00 TL" not in body


def test_recent_attempt_history_shows_only_latest_five_with_run_links(client) -> None:
	item = add_item("Attempt history", index=20)
	for day in range(6):
		add_attempt(item, status="failed", reason="parse_failed", at=NOW - timedelta(days=day))

	body = client.get("/data-quality").get_data(as_text=True)

	assert "Recent attempts (5)" in body
	assert body.count("Product data could not be parsed") >= 1


def test_retry_route_uses_watchlist_retry_service_and_reports_result(client, monkeypatch: pytest.MonkeyPatch) -> None:
	item = add_item("Retry route", index=30)
	finished_at = NOW + timedelta(minutes=1)
	called: list[int] = []

	def fake_update_watchlist_item(item_id: int):
		called.append(item_id)
		result_item = BatchScrapeItemResult(
			url=item.url,
			platform=item.platform,
			status="success",
			failure_reason=None,
			error_message=None,
			listing_id=1,
			product_name="Retry route",
			current_price=Decimal("999.00"),
			currency="TRY",
			started_at=NOW,
			finished_at=finished_at,
		)
		return BatchScrapeResult(
			run_id=77,
			status="completed",
			total=1,
			successful=1,
			failed=0,
			skipped=0,
			started_at=NOW,
			finished_at=finished_at,
			duration=finished_at - NOW,
			item_results=[result_item],
		)

	monkeypatch.setattr("app.routes.update_watchlist_item", fake_update_watchlist_item)
	response = client.post(
		f"/data-quality/{item.id}/retry",
		data={"return_to": "data_quality", "status": "stale", "sort": "oldest"},
		follow_redirects=True,
	)

	assert called == [item.id]
	assert "Retry succeeded in run ID 77." in response.get_data(as_text=True)


def test_data_quality_actions_preserve_context_and_do_not_delete_history(client) -> None:
	item = add_item("Action item", index=40)
	product = Product(name="Persistent product")
	listing = Listing(
		product=product,
		platform="trendyol",
		external_product_id=item.external_product_id,
		url=item.url,
		current_price=Decimal("500.00"),
		currency="TRY",
	)
	db.session.add_all([product, listing])
	db.session.commit()

	pause = client.post(
		f"/watchlist/{item.id}/toggle",
		data={"return_to": "data_quality", "status": "all", "sort": "oldest", "page": "1"},
	)
	client.post(f"/watchlist/{item.id}/delete", data={"return_to": "data_quality"})

	assert pause.status_code == 302
	assert "/data-quality" in pause.headers["Location"]
	assert "sort=oldest" in pause.headers["Location"]
	assert Product.query.count() == 1
	assert Listing.query.count() == 1
	assert WatchlistItem.query.count() == 0


def test_dashboard_includes_compact_data_quality_summary(client) -> None:
	item = add_item("Dashboard blocked", index=50)
	add_attempt(item, status="failed", reason="blocked_by_platform")

	body = client.get("/").get_data(as_text=True)

	assert "Data Quality" in body
	assert "View Data Quality" in body
	assert "Blocked" in body


def test_watchlist_includes_text_health_badge(client) -> None:
	item = add_item("Healthy badge", index=60)
	add_attempt(item, status="success", at=NOW)

	body = client.get("/watchlist").get_data(as_text=True)

	assert "health-badge--healthy" in body
	assert "Healthy" in body


def test_data_quality_escapes_unsafe_label_content(client) -> None:
	add_item("<script>alert('quality')</script>", index=70)

	body = client.get("/data-quality").get_data(as_text=True)

	assert "<script>alert('quality')</script>" not in body
	assert "&lt;script&gt;alert(&#39;quality&#39;)&lt;/script&gt;" in body
