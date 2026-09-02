"""Centralized paths for source runs and future/frozen desktop builds."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Mapping, MutableMapping


APP_DIRECTORY_NAME = "WatchPriceTracker"
DATABASE_FILENAME = "watch_tracker.db"


def is_frozen() -> bool:
	"""Return whether the process is running from a PyInstaller bundle."""
	return bool(getattr(sys, "frozen", False))


def get_source_root() -> Path:
	"""Return the repository/application source root."""
	return Path(__file__).resolve().parent.parent


def get_resource_root() -> Path:
	"""Return the read-only root containing templates, static assets, and packages."""
	bundle_root = getattr(sys, "_MEIPASS", None)
	if is_frozen() and bundle_root:
		return Path(bundle_root)
	return get_source_root()


def get_user_data_root(
	environ: Mapping[str, str] | None = None,
	*,
	home: Path | None = None,
) -> Path:
	"""Return the writable per-user application root without hard-coded usernames."""
	environment = os.environ if environ is None else environ
	local_app_data = environment.get("LOCALAPPDATA")
	if local_app_data:
		return Path(local_app_data) / APP_DIRECTORY_NAME
	if os.name == "nt":
		return (home or Path.home()) / "AppData" / "Local" / APP_DIRECTORY_NAME
	xdg_data_home = environment.get("XDG_DATA_HOME")
	if xdg_data_home:
		return Path(xdg_data_home) / APP_DIRECTORY_NAME
	return (home or Path.home()) / ".local" / "share" / APP_DIRECTORY_NAME


def get_data_directory(
	*,
	frozen: bool | None = None,
	environ: Mapping[str, str] | None = None,
	home: Path | None = None,
) -> Path:
	"""Keep source data in the repository and frozen data in writable app data."""
	if is_frozen() if frozen is None else frozen:
		return get_user_data_root(environ, home=home) / "data"
	return get_source_root() / "data"


def get_database_path(
	*,
	frozen: bool | None = None,
	environ: Mapping[str, str] | None = None,
	home: Path | None = None,
) -> Path:
	return get_data_directory(frozen=frozen, environ=environ, home=home) / DATABASE_FILENAME


def get_runtime_directory(
	environ: Mapping[str, str] | None = None,
	*,
	home: Path | None = None,
) -> Path:
	return get_user_data_root(environ, home=home) / "runtime"


def get_log_directory(
	environ: Mapping[str, str] | None = None,
	*,
	home: Path | None = None,
) -> Path:
	return get_user_data_root(environ, home=home) / "logs"


def get_playwright_browsers_path(*, frozen: bool | None = None) -> Path | None:
	"""Return bundled Chromium's root only when running from a frozen build."""
	if not (is_frozen() if frozen is None else frozen):
		return None
	return get_resource_root() / "playwright" / "driver" / "package" / ".local-browsers"


def configure_playwright_environment(
	environ: MutableMapping[str, str] | None = None,
	*,
	frozen: bool | None = None,
) -> Path | None:
	"""Point frozen Playwright at bundled Chromium without changing source runs."""
	browser_path = get_playwright_browsers_path(frozen=frozen)
	if browser_path is not None:
		(os.environ if environ is None else environ)["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_path)
	return browser_path
