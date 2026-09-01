"""Application settings for the Watch Price Tracker project."""

from pathlib import Path
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATABASE_PATH = DATA_DIR / "watch_tracker.db"


class Config:
	"""Base Flask configuration."""

	SQLALCHEMY_DATABASE_URI = f"sqlite:///{DATABASE_PATH.as_posix()}"
	SQLALCHEMY_TRACK_MODIFICATIONS = False
	MAX_CONTENT_LENGTH = 10 * 1024 * 1024
	SCRAPER_USER_AGENT = "WatchPriceTracker/1.0"
	DEFAULT_REQUEST_DELAY = 5
	ROBOTS_TIMEOUT = 5
	REQUEST_TIMEOUT = 10
	PLAYWRIGHT_TIMEOUT = 20
	IMPORT_PREVIEW_LIMIT = 20
	IMPORT_STATE_DIR = Path(tempfile.gettempdir()) / "watch-price-tracker-imports"
