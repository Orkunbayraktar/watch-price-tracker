"""Centralized application resource lookup for source and future frozen runs."""

from pathlib import Path
import sys


def get_resource_root() -> Path:
	"""Return the directory containing bundled templates and static assets."""
	bundle_root = getattr(sys, "_MEIPASS", None)
	if getattr(sys, "frozen", False) and bundle_root:
		return Path(bundle_root)
	return Path(__file__).resolve().parent.parent
