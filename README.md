# Watch Price Tracker

Watch Price Tracker is a Python-based project for tracking the prices of selected watch brands and models on Trendyol and Hepsiburada. The project is being developed as a university internship work-in-progress.

## Project Goal

The goal of this project is to monitor selected watch brands and models on Trendyol and Hepsiburada and keep track of:

- prices
- sellers
- seller-based prices
- price history

## Planned Features

- [ ] Trendyol scraper
- [ ] Hepsiburada scraper
- [x] robots.txt control
- [x] SQLite database
- [x] Product and seller records
- [x] Price history
- [ ] Flask web dashboard
- [ ] Product filtering
- [ ] Seller detail screen
- [ ] Price charts
- [ ] Manual data update
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
- PyInstaller

## Project Structure

```text
watch-price-tracker/
├── app/
├── config/
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

## Development Status

This project is an actively developed university internship project.
The current implementation includes a Trendyol single-product scraper proof of concept, not a full marketplace scraper.
The current implementation also includes a Hepsiburada single-product scraper proof of concept, not a full marketplace scraper.

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

## Important Note

Web scraping will be done with attention to:

- robots.txt
- each platform's terms of service
- controlled request pacing

This repository intentionally avoids storing secrets, credentials, or sensitive local data in version control.
