"""Route and rendering tests for the Scraping Control Center."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from app import create_app
from database.db import db, initialize_database
from database.models import Listing, Product, ScrapeRun, ScrapeRunItem, WatchlistItem
from services.batch_scraping_service import BatchScrapeItemResult, BatchScrapeResult
from services.scraping_control_service import ScrapingActionFailure, ScrapingActionResult


NOW = datetime.now(timezone.utc)
TRENDYOL_URL = "https://www.trendyol.com/casio/f-91w-p-1001"
HEPSIBURADA_URL = "https://www.hepsiburada.com/casio-retro-pm-hbcv00001"


@pytest.fixture
def client(tmp_path: Path):
	app = create_app(
		{
			"TESTING": True,
			"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
			"IMPORT_STATE_DIR": tmp_path / "import-state",
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
	url: str = TRENDYOL_URL,
	platform: str = "trendyol",
	is_active: bool = True,
) -> WatchlistItem:
	item = WatchlistItem(
		platform=platform,
		url=url,
		external_product_id=url.rsplit("-", 1)[-1],
		display_name=name,
		is_active=is_active,
	)
	db.session.add(item)
	db.session.commit()
	return item


def make_action_result(
	url: str = TRENDYOL_URL,
	*,
	status: str = "success",
	failure_reason: str | None = None,
	kind: str = "single",
) -> ScrapingActionResult:
	item = BatchScrapeItemResult(
		url=url,
		platform="hepsiburada" if "hepsiburada" in url else "trendyol",
		status=status,
		failure_reason=failure_reason,
		error_message=None,
		listing_id=14 if status == "success" else None,
		product_name="Casio F-91W" if status == "success" else None,
		current_price=Decimal("799.00") if status == "success" else None,
		currency="TRY" if status == "success" else None,
		started_at=NOW,
		finished_at=NOW + timedelta(minutes=1),
	)
	batch = BatchScrapeResult(
		run_id=77,
		status="completed" if status == "success" else "failed",
		total=1,
		successful=1 if status == "success" else 0,
		failed=0 if status == "success" else 1,
		skipped=0,
		started_at=NOW,
		finished_at=NOW + timedelta(minutes=1),
		duration=timedelta(minutes=1),
		item_results=[item],
	)
	failures = [] if status == "success" else [
		ScrapingActionFailure(
			url=url,
			platform=item.platform,
			failure_reason=failure_reason or "other_failure",
			failure_message="Platform blocked browser request" if failure_reason == "blocked_by_platform" else "Scrape attempt failed",
		)
	]
	return ScrapingActionResult(kind=kind, title="Test Operation", batch_result=batch, failed_items=failures)


def test_control_center_page_loads_and_renders_empty_states(client) -> None:
	response = client.get("/scraping")
	body = response.get_data(as_text=True)

	assert response.status_code == 200
	assert "Scraping Control Center" in body
	assert "No products are currently being tracked." in body
	assert "No scraping runs have been recorded yet." in body
	assert "No recent scraping failures." in body


def test_summary_values_and_watchlist_rows_use_real_database_data(client) -> None:
	add_item("Active Casio")
	add_item("Paused Casio", url=HEPSIBURADA_URL, platform="hepsiburada", is_active=False)
	run = ScrapeRun(
		platform="trendyol",
		status="completed_with_errors",
		started_at=NOW,
		finished_at=NOW + timedelta(minutes=2),
		products_found=2,
		listings_found=2,
		errors_count=1,
	)
	db.session.add(run)
	db.session.commit()

	body = client.get("/scraping").get_data(as_text=True)

	assert "Active Products" in body and "Paused Products" in body
	assert "Active Casio" in body and "Paused Casio" in body
	assert f"#{run.id}" in body
	assert "Successful Last Run Items" in body
	assert "Failed Last Run Items" in body


def test_latest_and_missing_prices_render_safely(client) -> None:
	priced = add_item("Priced Casio")
	add_item("No Price Casio", url="https://www.trendyol.com/casio/no-price-p-1002")
	product = Product(name="Casio F-91W")
	listing = Listing(
		product=product,
		platform="trendyol",
		external_product_id=priced.external_product_id,
		url=priced.url,
		current_price=Decimal("1234.50"),
		currency="TRY",
	)
	db.session.add_all([product, listing])
	db.session.commit()

	body = client.get("/scraping").get_data(as_text=True)

	assert "1.234,50 TL" in body
	assert "No price data" in body
	assert "0,00 TL" not in body


def test_update_active_action_uses_control_service_and_shows_batch_summary(client, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr("app.routes.update_all_active_products", lambda: make_action_result(kind="active"))

	response = client.post("/scraping/update-active")
	body = response.get_data(as_text=True)

	assert response.status_code == 200
	assert "Run ID 77 finished: 1 successful, 0 failed." in body
	assert "Completed Operation" in body
	assert "Open Run Detail" in body


def test_update_selected_passes_only_submitted_ids_to_control_service(client, monkeypatch: pytest.MonkeyPatch) -> None:
	captured: list[list[str]] = []

	def fake_update_selected(item_ids):
		captured.append(item_ids)
		return make_action_result(kind="selected")

	monkeypatch.setattr("app.routes.update_selected_products", fake_update_selected)
	client.post("/scraping/update-selected", data={"item_ids": ["3", "7"]})

	assert captured == [["3", "7"]]


def test_empty_selected_action_is_handled_safely(client) -> None:
	response = client.post("/scraping/update-selected")

	assert "Select at least one active product to update." in response.get_data(as_text=True)
	assert ScrapeRun.query.count() == 0


def test_selected_paused_item_is_reported_as_skipped_and_stays_paused(client) -> None:
	paused = add_item("Paused", is_active=False)

	response = client.post("/scraping/update-selected", data={"item_ids": str(paused.id)})

	assert "paused item(s) were skipped" in response.get_data(as_text=True)
	assert db.session.get(WatchlistItem, paused.id).is_active is False
	assert ScrapeRun.query.count() == 0


def test_manual_single_success_renders_useful_result(client, monkeypatch: pytest.MonkeyPatch) -> None:
	called: list[str] = []
	monkeypatch.setattr(
		"app.routes.scrape_one_product",
		lambda url: called.append(url) or make_action_result(url, kind="single"),
	)

	response = client.post("/scraping/scrape-one", data={"url": TRENDYOL_URL})
	body = response.get_data(as_text=True)

	assert called == [TRENDYOL_URL]
	assert "Casio F-91W" in body
	assert "799,00 TL" in body
	assert "Listing ID" in body


def test_hepsiburada_blocked_failure_is_displayed_clearly(client, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr(
		"app.routes.scrape_one_product",
		lambda _url: make_action_result(
			HEPSIBURADA_URL,
			status="failed",
			failure_reason="blocked_by_platform",
			kind="single",
		),
	)

	body = client.post("/scraping/scrape-one", data={"url": HEPSIBURADA_URL}).get_data(as_text=True)

	assert "Platform blocked browser request" in body
	assert "blocked_by_platform" in body
	assert "Hepsiburada" in body


@pytest.mark.parametrize("url", ["https://example.com/watch", "javascript:alert(1)", "file:///tmp/watch"])
def test_manual_invalid_urls_are_rejected_without_creating_run(client, url: str) -> None:
	response = client.post("/scraping/scrape-one", data={"url": url})

	assert response.status_code == 200
	assert ScrapeRun.query.count() == 0
	assert "supported" in response.get_data(as_text=True) or "HTTP" in response.get_data(as_text=True)


def test_recent_runs_failures_and_existing_detail_links_render(client) -> None:
	tracked = add_item("Blocked Watch", url=HEPSIBURADA_URL, platform="hepsiburada")
	run = ScrapeRun(
		platform="hepsiburada",
		status="failed",
		started_at=NOW,
		finished_at=NOW + timedelta(minutes=1),
		errors_count=1,
	)
	attempt = ScrapeRunItem(
		scrape_run=run,
		url=tracked.url,
		platform="hepsiburada",
		status="failed",
		failure_reason="blocked_by_platform",
		started_at=NOW,
		finished_at=NOW + timedelta(minutes=1),
	)
	db.session.add_all([run, attempt])
	db.session.commit()

	response = client.get("/scraping")
	body = response.get_data(as_text=True)

	assert "Recent Runs" in body and "Recent Failures" in body
	assert "Blocked Watch" in body
	assert "blocked_by_platform" in body
	assert f'/scrape-runs/{run.id}' in body
	assert client.get(f"/scrape-runs/{run.id}").status_code == 200


def test_running_run_disables_actions_and_backend_blocks_update(client) -> None:
	run = ScrapeRun(platform="trendyol", status="running", started_at=NOW)
	db.session.add(run)
	db.session.commit()

	page_body = client.get("/scraping").get_data(as_text=True)
	response = client.post("/scraping/update-active")

	assert "A scraping run is already in progress." in page_body
	assert "disabled" in page_body
	assert "already in progress" in response.get_data(as_text=True)
	assert ScrapeRun.query.count() == 1


def test_watchlist_actions_return_to_control_center_and_preserve_history(client) -> None:
	item = add_item("Action Watch")
	product = Product(name="Persistent Product")
	listing = Listing(product=product, platform="trendyol", url=item.url, external_product_id=item.external_product_id, current_price=Decimal("500.00"), currency="TRY")
	db.session.add_all([product, listing])
	db.session.commit()

	pause = client.post(f"/watchlist/{item.id}/toggle", data={"return_to": "scraping"})
	remove = client.post(f"/watchlist/{item.id}/delete", data={"return_to": "scraping"})

	assert pause.headers["Location"].endswith("/scraping")
	assert remove.headers["Location"].endswith("/scraping")
	assert Product.query.count() == 1
	assert Listing.query.count() == 1


def test_product_discovery_placeholder_has_no_crawling_action(client, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr("services.scraping_control_service.run_batch", lambda *_args, **_kwargs: pytest.fail("GET must not scrape"))
	response = client.get("/scraping")
	soup = BeautifulSoup(response.get_data(as_text=True), "html.parser")
	discovery_heading = soup.find("h2", string="Product Discovery")
	discovery_card = discovery_heading.find_parent("article")

	assert "Coming soon" in discovery_card.get_text(" ", strip=True)
	assert discovery_card.find("form") is None
	assert discovery_card.find("button") is None


def test_unsafe_watchlist_label_is_escaped_on_control_center(client) -> None:
	add_item("<script>alert('control')</script>")

	body = client.get("/scraping").get_data(as_text=True)

	assert "<script>alert('control')</script>" not in body
	assert "&lt;script&gt;alert(&#39;control&#39;)&lt;/script&gt;" in body
