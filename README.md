# Watch Price Tracker

Watch Price Tracker is a Python-based project for tracking the prices of selected watch brands and models on Trendyol, Hepsiburada, and Saat&Saat. The project is being developed as a university internship work-in-progress.

## Project Goal

The goal of this project is to monitor selected watch brands and models across supported marketplaces and keep track of:

- prices
- sellers
- seller-based prices
- price history

## Planned Features

- [x] Trendyol scraper
- [x] Hepsiburada scraper
- [x] Saat&Saat product provider (live status pending manual verification)
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
The Saat&Saat single-product provider is integrated, but live acquisition remains pending manual verification against a current public product page.
The local Flask interface now includes a read-only analytics dashboard with database-backed product, seller, and import views.
It also includes a watchlist management screen for tracking supported marketplace product URLs directly from the browser.

## Data Sources

Current data sources:

- Web scraping adapters for Trendyol, Hepsiburada, and Saat&Saat product pages
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
- All supported product providers use the same generic live path: robots.txt gate -> fetcher -> rendered HTML parser -> `ScrapedProductData` -> generic persistence -> SQLite.
- Trendyol: Playwright live acquisition is the established live path.
- Hepsiburada: Playwright support stays inside the shared flow and may be blocked by the platform. The parser can persist seller-less listings when the public page does not visibly expose seller information.
- Saat&Saat: the single-product provider uses the same Playwright-capable flow and can persist seller-less listings; live status remains pending manual verification.
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

- The `/watchlist` page lets you add, pause, reactivate, and remove tracked Trendyol, Hepsiburada, and Saat&Saat product URLs through the local Flask UI.
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

## Data Quality

- The `/data-quality` page provides operational visibility for every tracked URL. It supports validated health, platform, active/paused, search, and sort controls with server-side pagination.
- Each watchlist item receives one mutually exclusive primary health state. The latest failed attempt maps its existing structured reason to `blocked`, `robots_denied`, `parse_failed`, `persistence_failed`, `invalid_data`, `unsupported_url`, or `other_failure`. A successful active item is `stale` when its last successful attempt exceeds `DATA_QUALITY_STALE_HOURS`; otherwise it is `healthy`. Items with no recorded outcome are `never_scraped`.
- Summary health counts cover active tracked items only. `blocked` is reported separately from `failed`; the failed total combines parser, persistence, robots, invalid-data, unsupported-URL, and other failures. Paused items remain visible through filters but do not inflate active health totals or become stale.
- The default stale threshold is 48 hours and can be changed through `DATA_QUALITY_STALE_HOURS`. Page size defaults to 25 through `DATA_QUALITY_PAGE_SIZE`.
- Problem rows show the latest scrape state, a concise failure message, the original structured failure reason, the latest known persisted price when available, and up to five recent `ScrapeRunItem` attempts with links to their runs. Missing price data is shown neutrally and is never represented as zero.
- Manual retry runs one active item through the existing Playwright watchlist/batch path, creates the normal `ScrapeRun` and `ScrapeRunItem` records, and refreshes watchlist status metadata. Platform blocks, including Hepsiburada blocking, remain recorded failures; no bypass or automatic retry is attempted.
- Data Quality and Watchlist views bulk-load attempt and listing context in fixed query sets rather than issuing per-row queries. The main dashboard includes a compact healthy, stale, blocked, and failed summary with a link to the full page.

## Scraping Control Center

- The `/scraping` page centralizes normal synchronous scraping operations in the Flask interface. It shows real active and paused Watchlist counts, the latest run, recent runs, structured failures, latest known prices, and Data Quality health states.
- `Update Active Products` sends every active Watchlist URL through the existing Watchlist service and sequential batch workflow. `Update Selected` accepts checkbox-selected items, updates only active selections, and reports paused selections as skipped without activating them.
- `Scrape One Product` validates a supported Trendyol, Hepsiburada, or Saat&Saat product URL and sends it directly through the existing batch orchestration, ScrapeRun tracking, scraper, persistence, Listing, and PriceHistory flow. The URL does not need to be added to the Watchlist.
- Control Center scraping defaults to Playwright, preserving the established Trendyol live-acquisition path. Hepsiburada may still return HTTP 403 or `blocked_by_platform`; these outcomes remain structured failures and are displayed without attempting a bypass.
- A database check prevents a new Control Center operation when a ScrapeRun is already marked `running`. Manual execution remains synchronous and local; automatic execution uses the same guard and remains sequential.
- Recent Runs links to the existing ScrapeRun detail page, while Recent Failures reuses Data Quality's concise failure-reason mapping. Watchlist health, pause/activate, retry, product navigation, and non-destructive removal continue to use their existing services and routes.
- Lightweight browser behavior disables submitted scraping buttons and shows an in-progress label to reduce accidental double-clicks. Backend validation and running-run checks remain authoritative.
- The Product Discovery card links to the bounded Brand Discovery workflow. Discovery remains separate from normal scraping and only selected preview URLs enter the Watchlist.

## Automatic Scheduling

- The `/scheduler` page creates, edits, enables, disables, deletes, and immediately runs multiple daily Watchlist schedules. Daily times are interpreted with the `Europe/Istanbul` timezone rather than a fixed UTC offset.
- Every automatic run and `Run Now` action calls the existing active Watchlist update service. That service excludes paused items and reuses the established Playwright, batch, `ScrapeRun`, persistence, `PriceHistory`, and Watchlist metadata pipeline.
- Schedule definitions and their latest outcome metadata are stored in the `scrape_schedules` table. Enabled schedules are restored into APScheduler when `python run.py` starts the application.
- The local application must remain open and running for jobs to execute. Scheduling does not continue while the executable or Flask process is closed.
- Jobs use a 60-second misfire grace period with coalescing and one instance per job. Runs missed while the application is closed are not aggressively replayed after restart.
- A process-level scheduler lock and the existing database running-run check prevent overlapping automatic scraping. Busy attempts are recorded as `skipped_busy`; an empty active Watchlist is recorded as `skipped_no_active_items` without creating a `ScrapeRun`.
- Active Saat&Saat items join Trendyol and Hepsiburada in the same Watchlist batch; no marketplace-specific scheduler job is created.
- Individual marketplace failures remain normal item-level outcomes. Hepsiburada or Saat&Saat may report HTTP 403 or `blocked_by_platform`; neither behavior is bypassed by scheduling.
- The development launcher starts APScheduler once and disables Werkzeug's duplicate reloader process. The Flask application factory itself does not start background work, so `TESTING=True` and pytest remain deterministic.
- Deleting a schedule removes only its configuration and in-memory job. Existing marketplace data, price history, and `ScrapeRun` history remain intact.

### Manual Scheduler Test

1. Start the local application with `python run.py` and keep that terminal and application process open.
2. Open `/scheduler` and create an enabled daily schedule a few minutes in the future using Türkiye local time.
3. Confirm the schedule appears as Enabled and has the expected Next Run value.
4. Wait for the scheduled time, then open Scrape Runs and verify a normal `ScrapeRun` was created automatically.
5. Confirm active Watchlist items received updated status metadata and successful Trendyol listings gained a new `PriceHistory` observation.
6. Return to `/scheduler` and verify Last Run, Last Result, the linked ScrapeRun, and the following day's Next Run.
7. If a Hepsiburada item is blocked, verify it appears as a normal item failure and that the schedule remains enabled for its next run.

## Brand Discovery / Catalog Import

- The `/discovery` page lets users choose Trendyol or Hepsiburada, enter a brand, and preview public marketplace product-card metadata before adding anything.
- Trendyol is the primary discovery provider. It builds provider-controlled public search URLs, scans pages sequentially, and applies the configured `DISCOVERY_MAX_PAGES` and `DISCOVERY_MAX_PRODUCTS` limits.
- Hepsiburada uses the same provider contract. Live discovery may return `blocked_by_platform`; this is reported as a normal operational restriction and no bypass is attempted.
- Every discovery page is checked through the existing fail-safe robots.txt manager before fetching. A robots denial or load failure prevents the marketplace request.
- Preview results are kept temporarily in signed, expiring server-side files. The Flask cookie never contains the discovered catalog payload.
- Already tracked canonical URLs are marked and cannot be selected. New selections are validated and added through the existing Watchlist service, so duplicate handling stays centralized.
- Discovery prices and names are preview metadata only. They do not create `Product`, `Listing`, or `PriceHistory` records; normal Watchlist or Control Center scraping remains responsible for detailed persistence.
- After import, `Update Added Products` is available as a separate explicit action. Discovery never automatically scrapes or adds the full result set.

## Settings

- The `/settings` page exposes only three bounded, non-sensitive values: the Data Quality stale threshold, Brand Discovery page limit, and default discovery product limit.
- Values are validated server-side and stored as allowlisted rows in the `app_settings` table. Environment-sensitive values, browser identity, proxy behavior, and anti-bot options are not editable in the UI.
- Data Quality and Brand Discovery read the effective persisted settings immediately. Discovery remains sequential and its hard product ceiling remains 200.
- `Restore Default Settings` removes only these editable overrides and returns to the configured Python defaults. It does not delete marketplace, Watchlist, price, or scrape data.

## Data Management

All cleanup actions are POST-only, use named transactional service operations, report real deleted counts, and roll back completely if any step fails. They are rejected while a `ScrapeRun` is marked `running`.

- **Clear Price History** deletes `PriceHistory` observations. It preserves products, sellers, listings, listing current prices, Watchlist items, scrape history, and settings.
- **Clear Scrape History** deletes `ScrapeRunItem` and `ScrapeRun` records. It preserves marketplace data, price history, Watchlist items, settings, and Scheduler configuration; schedule links to deleted runs are set to null.
- **Clear Marketplace Data** deletes price history, listings, sellers, and products. It preserves Watchlist URLs, scrape history, and settings; Watchlist scrape timestamps/status/failure metadata reset so retained URLs correctly appear never scraped and can rebuild the dataset through the existing update flow.
- **Clear Watchlist** deletes monitoring configuration only. Products, sellers, listings, price history, scrape history, and settings remain.
- **Reset All Data** deletes products, sellers, listings, price history, Watchlist items, scrape runs, and run items. It preserves the SQLite schema, saved application settings, and Scheduler configuration; schedule links to deleted runs are set to null. The backend requires the exact typed confirmation `RESET`.
- Brand Discovery previews are temporary signed file-backed state, not database history, so no discovery table cleanup is needed.

The current local Flask application does not include CSRF middleware. This change does not introduce authentication or a larger security framework; destructive actions remain POST-only with explicit dialogs and backend confirmation validation. Do not expose the development server to untrusted networks.

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
.\.venv\Scripts\python.exe scripts\smoke_test_live.py --platform saatvesaat --fetcher playwright "<SAAT_VE_SAAT_PRODUCT_URL>"
```

For browser debugging you can add `--headed`, but headless mode remains the default and preferred smoke-test path.

Run read-only brand discovery smoke tests without adding products to the Watchlist:

```powershell
.\.venv\Scripts\python.exe scripts\discover_products.py --platform trendyol --brand "Casio" --max-products 20
.\.venv\Scripts\python.exe scripts\discover_products.py --platform hepsiburada --brand "Casio" --max-products 20
```

Both commands check robots.txt, use controlled sequential Playwright requests, print only a compact result summary and the first few products, and never print page HTML. A blocked Hepsiburada request is reported as `blocked_by_platform`.

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
- Saat&Saat URLs use the same controlled batch path; live support remains pending manual verification

The batch CLI prints a compact start summary, one line per processed URL, and a final result summary with the stored ScrapeRun ID.

## Local Database Schema Changes

The local SQLite database file is created with `create_all()`. Changing a SQLAlchemy model later does not rewrite an existing SQLite table definition.

Watchlist management adds a new `watchlist_items` table. If your local development database was created before this table existed, start the app through `python run.py` or run any helper that calls `initialize_database(app)` so `db.create_all()` can create the missing table.

Batch scraping adds a new `scrape_run_items` table. If your local development database was created before this table existed, start the app or run any script path that calls `initialize_database(app)` so `db.create_all()` can create the missing table.

The existing `scrape_runs` model is also now used for real operational tracking. No silent database deletion is performed by this change.

Saat&Saat support uses the existing string platform columns and generic records. It requires no database migration or database recreation.

Brand Discovery adds no database tables or columns. Its unselected preview data expires from temporary server-side storage, while selected URLs use the existing `watchlist_items` table.

Settings adds a new `app_settings` table only. Starting the app through `python run.py`, or another path that calls `initialize_database(app)`, runs `db.create_all()` and creates the table without deleting or recreating the existing SQLite database. No manual migration or data reset is required for this additive table.

Automatic Scheduling adds the `scrape_schedules` table only. Install the updated requirements, then start the app through `python run.py`; the existing `initialize_database(app)` / `db.create_all()` path creates this table without deleting, recreating, or modifying collected Product, Watchlist, Listing, or PriceHistory data.

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
