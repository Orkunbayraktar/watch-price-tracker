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
	if is_frozen():
		candidates: list[Path] = []
		bundle_root = getattr(sys, "_MEIPASS", None)
		if bundle_root:
			candidates.append(Path(bundle_root))
		executable_directory = Path(sys.executable).resolve().parent
		candidates.extend((executable_directory / "_internal", executable_directory))
		for candidate in candidates:
			if _contains_application_resources(candidate):
				return candidate
		if candidates:
			return candidates[0]
	return get_source_root()


def resource_path(relative_path: str | Path) -> Path:
	"""Resolve one safe application resource below the active resource root."""
	relative = Path(relative_path)
	if relative.is_absolute() or ".." in relative.parts:
		raise ValueError("Application resource paths must be relative and cannot traverse parents.")
	return get_resource_root() / relative


def get_template_directory() -> Path:
	"""Return the directory containing Jinja templates."""
	return resource_path("templates")


def get_static_directory() -> Path:
	"""Return the recursively bundled Flask static directory."""
	return resource_path("static")


def count_resource_files(directory: Path) -> int:
	"""Return a diagnostic file count, or zero when a resource directory is absent."""
	if not directory.is_dir():
		return 0
	try:
		return sum(1 for path in directory.rglob("*") if path.is_file())
	except OSError:
		return 0


def _contains_application_resources(candidate: Path) -> bool:
	return (candidate / "templates").is_dir() and (candidate / "static").is_dir()


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
