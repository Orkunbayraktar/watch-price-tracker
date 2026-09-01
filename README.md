# Watch Price Tracker

Watch Price Tracker is a Python-based project for tracking the prices of selected watch brands and models on Trendyol and Hepsiburada. The project is being developed as a university internship work-in-progress.

## Project Goal

The goal of this project is to monitor selected watch brands and models on Trendyol and Hepsiburada and keep track of:

- prices
- sellers
- seller-based prices
- price history

## Planned Features

- [x] Trendyol scraper
- [x] Hepsiburada scraper
- [x] robots.txt control
- [x] SQLite database
- [x] Product and seller records
- [x] Price history
- [x] Flask web dashboard
- [x] Watchlist management UI
- [x] Product filtering
- [x] Seller detail screen
- [x] Price charts
- [x] Price intelligence
- [x] Manual data update
- [ ] Windows executable

## Tech Stack

- Python
- Flask
- SQLite
- SQLAlchemy
- HTML
- CSS
- JavaScript
- BeautifulSoup / requests or suitable scraping tools
- Playwright for controlled browser-based feasibility checks
- PyInstaller

## Project Structure

```text
watch-price-tracker/
├── app/
├── config/
├── data_sources/
├── data/
│   ├── exports/
│   └── raw/
├── database/
├── scrapers/
├── services/
├── static/
├── templates/
├── tests/
├── launcher.py
├── README.md
├── requirements.txt
└── run.py
```

## Installation

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install dependencies and, if you want to test browser-based live acquisition, install Chromium for Playwright:

```powershell
pip install -r requirements.txt
python -m playwright install chromium
```

## Development Status

This project is an actively developed university internship project.
The current implementation includes a Trendyol single-product scraper proof of concept, not a full marketplace scraper.
The current implementation also includes a Hepsiburada single-product scraper proof of concept, not a full marketplace scraper.
The local Flask interface now includes a read-only analytics dashboard with database-backed product, seller, and import views.
It also includes a watchlist management screen for tracking supported marketplace product URLs directly from the browser.

## Data Sources

Current data sources:

- Web scraping adapters for Trendyol and Hepsiburada product pages
- CSV import into the shared normalized product format
- Excel `.xlsx` import into the shared normalized product format

Planned data sources:

- Authorized marketplace APIs
- Additional approved institutional data feeds

All current data sources normalize incoming records into the same `ScrapedProductData` structure before persistence.
The persistence layer does not need to know whether data came from a scraper, a CSV file, or an Excel file.

The web scraping adapters can be blocked by platform-side HTTP 403 or rate limiting responses.
This project does not attempt to bypass platform protections, fake browser identities, or evade anti-bot controls.

## Live Data Acquisition

- The scraper layer supports explicit fetch strategies: `requests` for direct HTTP fetching and `playwright` for controlled Chromium-based browser fetching.
- Browser fetching is opt-in. The code never silently switches from `requests` to `playwright` after a 403 or another failure.
- Existing `robots.txt` checks still run before any product request. If robots access is denied or robots loading fails, the browser is not launched.
- Browser mode uses normal Playwright Chromium only. No stealth plugin, CAPTCHA solving, proxy rotation, fingerprint spoofing, or private marketplace API replay is implemented.
- Challenge and blocking pages are detected and reported as structured failures. The project does not try to solve or bypass those pages.
- Both marketplaces use the same generic live path: robots.txt gate -> fetcher -> rendered HTML parser -> `ScrapedProductData` -> generic persistence -> SQLite.
- Hepsiburada Playwright support stays inside that shared flow. The Hepsiburada parser can persist seller-less listings when the public product page does not visibly expose seller information.
- A concrete Hepsiburada live success should only be claimed after you run the manual smoke test locally against a real product URL.
- Marketplace pages may still block anonymous browser automation even when a normal browser renderer is used.
- CSV and XLSX import remain supported as a stable fallback data source when live acquisition is blocked.

### File Import

- CSV and XLSX uploads are supported through the local Flask interface.
- Each uploaded file is validated and previewed before any database write happens.
- Only valid rows are imported into the database.
- Existing Product, Seller, and Listing records are matched through the current persistence logic, so duplicate main records are not created when the same file is imported again.

## Analytics Dashboard

- The `/` route shows real summary metrics, biggest price drops, biggest price increases, recent real price changes, recently updated listings, and a marketplace breakdown.
- The `/products` route supports real database-backed search, brand filtering, platform filtering, safe sorting, and server-side pagination.
- The `/sellers` route supports seller search, platform filtering, and server-side pagination.
- Product and seller detail pages expose current listing records and linked navigation between related entities.

## Watchlist

- The `/watchlist` page lets you add, pause, reactivate, and remove tracked Trendyol and Hepsiburada product URLs through the local Flask UI.
- Watchlist entries represent monitored URLs only. Removing or pausing a watchlist item does not delete persisted `Product`, `Listing`, or `PriceHistory` records.
- URL validation reuses the existing scraper registry and supported product URL checks, so unsupported domains, malformed URLs, and unsafe schemes are rejected before storage.
- Duplicate tracked URLs are prevented through conservative canonicalization: whitespace is trimmed, scheme and host casing are normalized, and URL fragments are removed while seller/product query parameters remain intact when present.
- If a tracked URL already has matching database records, the watchlist page can show the latest known product link and price context without requiring a hard foreign key from watchlist items to products.

## Manual Watchlist Updates

- The watchlist page includes an `Update Active Products` action that runs a synchronous batch update for active tracked URLs.
- Manual watchlist updates reuse the existing `ScrapeRun` and `ScrapeRunItem` infrastructure, so each run stores batch-level totals, duration, and per-item outcomes in the same operational history used by the batch CLI.
- Trendyol watchlist updates default to the `playwright` fetch strategy because requests-based live acquisition returned HTTP 403 in real testing.
- Hepsiburada updates stay inside the existing browser-based acquisition path. If the platform returns blocking pages or HTTP 403, the watchlist item remains tracked and the structured failure reason is stored.
- After every attempted update, the tracked item stores `last_scraped_at`, `last_scrape_status`, and `last_failure_reason` for quick visibility in the UI.

## Price Intelligence

- Price history observations are preserved exactly as recorded; analytics separately derive real price changes by skipping consecutive duplicate prices.
- Product detail pages now show previous distinct price comparison, change percentage, observed minimum and maximum prices, observed average price, seller comparison, and a paginated observation history table.
- Product-level analytics include the current cheapest listing, current price spread, seller count, active listing count, and current average price.
- The dashboard highlights the biggest recent price drops, biggest recent increases, and the most recent real price changes from the last 7 days.
- The product detail page includes an offline-safe native SVG price history chart, so the local Flask app does not depend on a CDN or frontend build system.

## Development Smoke Test

Use the following command from the project root to run a manual end-to-end smoke test for a single Trendyol product URL:

```bash
python scripts/smoke_test_trendyol.py "<TRENDYOL_PRODUCT_URL>"
```

Use the following command from the project root to run the equivalent Hepsiburada smoke test:

```bash
python scripts/smoke_test_hepsiburada.py "<HEPSIBURADA_PRODUCT_URL>"
```

This command is intended only for development and debugging. It validates the scraper, robots.txt checks, persistence flow, and SQLite storage for one real product URL.

Use the following generic live smoke test when you want to explicitly choose the fetcher strategy:

```powershell
python scripts/smoke_test_live.py --platform trendyol --fetcher requests "<TRENDYOL_PRODUCT_URL>"
python scripts/smoke_test_live.py --platform trendyol --fetcher playwright "<TRENDYOL_PRODUCT_URL>"
python scripts/smoke_test_live.py --platform hepsiburada --fetcher playwright "<HEPSIBURADA_PRODUCT_URL>"
```

For browser debugging you can add `--headed`, but headless mode remains the default and preferred smoke-test path.

## Batch Scraping

Use the following command from the project root to run a controlled sequential batch scrape from a text file:

```powershell
python scripts/batch_scrape.py --platform trendyol --fetcher playwright urls.txt
```

Batch scraping currently runs sequentially on purpose. Each URL still goes through the existing marketplace scraper, robots.txt gate, selected fetcher, parser, and generic persistence service.

- blank lines and `#` comment lines in the URL file are ignored
- duplicate URLs inside the same batch are skipped after conservative normalization
- per-item failures do not roll back successful items from the same run
- batch metadata and item-level results are stored through `ScrapeRun` and `ScrapeRunItem`
- Hepsiburada URLs can be included, but platform-side HTTP 403 responses are recorded as normal failures and are not bypassed

The batch CLI prints a compact start summary, one line per processed URL, and a final result summary with the stored ScrapeRun ID.

## Local Database Schema Changes

The local SQLite database file is created with `create_all()`. Changing a SQLAlchemy model later does not rewrite an existing SQLite table definition.

Watchlist management adds a new `watchlist_items` table. If your local development database was created before this table existed, start the app through `python run.py` or run any helper that calls `initialize_database(app)` so `db.create_all()` can create the missing table.

Batch scraping adds a new `scrape_run_items` table. If your local development database was created before this table existed, start the app or run any script path that calls `initialize_database(app)` so `db.create_all()` can create the missing table.

The existing `scrape_runs` model is also now used for real operational tracking. No silent database deletion is performed by this change.

If your existing local `data/watch_tracker.db` was created while `listings.seller_id` was still `NOT NULL`, you must recreate that local database before live smoke tests can persist seller-less listings.

The project now includes a safe development helper that backs up the current database file before recreating it:

```powershell
python scripts/recreate_local_db.py --confirm-delete
```

Without `--confirm-delete`, the script only prints the target path and exits without modifying anything.

## Important Note

Web scraping will be done with attention to:

- robots.txt
- each platform's terms of service
- controlled request pacing

This repository intentionally avoids storing secrets, credentials, or sensitive local data in version control.
