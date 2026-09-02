"""Reproducible Windows onedir build for the desktop application."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

import playwright


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = PROJECT_ROOT / "WatchPriceTracker.spec"
PLAYWRIGHT_PACKAGE = Path(playwright.__file__).resolve().parent
HERMETIC_BROWSER_DIR = PLAYWRIGHT_PACKAGE / "driver" / "package" / ".local-browsers"
EXPECTED_EXE = PROJECT_ROOT / "dist" / "WatchPriceTracker" / "WatchPriceTracker.exe"


def main() -> int:
	parser = argparse.ArgumentParser(description="Build the Watch Price Tracker Windows onedir application.")
	parser.add_argument("--clean", action="store_true", help="Remove repository build/dist output before building.")
	parser.add_argument(
		"--skip-browser-install",
		action="store_true",
		help="Use an existing hermetic Playwright Chromium installation.",
	)
	args = parser.parse_args()

	if os.name != "nt":
		parser.error("The Windows executable must be built on Windows.")
	if not SPEC_PATH.is_file():
		parser.error(f"Missing maintained spec file: {SPEC_PATH}")
	if args.clean:
		_remove_build_output(PROJECT_ROOT / "build")
		_remove_build_output(PROJECT_ROOT / "dist")

	environment = os.environ.copy()
	environment["PLAYWRIGHT_BROWSERS_PATH"] = "0"
	if not args.skip_browser_install:
		_run([sys.executable, "-m", "playwright", "install", "chromium"], environment)
	if not _has_bundled_chromium():
		parser.error(
			"Hermetic Chromium is missing. Run without --skip-browser-install so Playwright can install it."
		)

	_run(
		[sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC_PATH)],
		environment,
	)
	if not EXPECTED_EXE.is_file():
		raise RuntimeError(f"PyInstaller completed without creating {EXPECTED_EXE}")
	print(f"Built: {EXPECTED_EXE}")
	print(f"Distribution size: {_directory_size_mb(EXPECTED_EXE.parent):.1f} MiB")
	return 0


def _has_bundled_chromium() -> bool:
	return HERMETIC_BROWSER_DIR.is_dir() and any(
		path.is_dir() and path.name.startswith(("chromium-", "chromium_headless_shell-"))
		for path in HERMETIC_BROWSER_DIR.iterdir()
	)


def _remove_build_output(path: Path) -> None:
	resolved_root = PROJECT_ROOT.resolve()
	resolved_path = path.resolve()
	if resolved_path.parent != resolved_root or resolved_path.name not in {"build", "dist"}:
		raise RuntimeError(f"Refusing to remove unexpected path: {resolved_path}")
	if resolved_path.exists():
		shutil.rmtree(resolved_path)


def _run(command: list[str], environment: dict[str, str]) -> None:
	print(f"Running: {subprocess.list2cmdline(command)}")
	subprocess.run(command, cwd=PROJECT_ROOT, env=environment, check=True)


def _directory_size_mb(path: Path) -> float:
	return sum(file.stat().st_size for file in path.rglob("*") if file.is_file()) / (1024 * 1024)


if __name__ == "__main__":
	raise SystemExit(main())
