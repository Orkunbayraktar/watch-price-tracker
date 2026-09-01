"""Safely recreate the local SQLite development database."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
	sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from config.settings import DATABASE_PATH
from database.db import initialize_database


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(
		description="Back up the local SQLite database file and recreate it with the current schema.",
	)
	parser.add_argument(
		"--confirm-delete",
		action="store_true",
		help="Required to move the existing database aside and recreate it.",
	)
	return parser


def _build_backup_path(database_path: Path) -> Path:
	timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
	return database_path.with_name(f"{database_path.stem}.{timestamp}.bak{database_path.suffix}")


def recreate_local_database() -> int:
	args = build_parser().parse_args()
	database_path = DATABASE_PATH

	print(f"Local database path: {database_path}")
	if not args.confirm_delete:
		print("No changes were made. Re-run with --confirm-delete to back up and recreate the local database.")
		return 1

	database_path.parent.mkdir(parents=True, exist_ok=True)
	if database_path.exists():
		backup_path = _build_backup_path(database_path)
		shutil.move(str(database_path), str(backup_path))
		print(f"Existing database moved to: {backup_path}")
	else:
		print("No existing database file was found. A new database will be created.")

	app = create_app()
	initialize_database(app)
	print(f"Recreated database with current schema: {database_path}")
	return 0


if __name__ == "__main__":
	sys.exit(recreate_local_database())