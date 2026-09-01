"""Shared URL normalization helpers for tracked marketplace product URLs."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def normalize_tracking_url(url: str) -> str:
	"""Trim and conservatively normalize a marketplace URL for tracking."""
	trimmed = url.strip()
	if not trimmed:
		return ""

	parts = urlsplit(trimmed)
	if not parts.scheme or not parts.netloc:
		return trimmed

	return urlunsplit(
		(
			parts.scheme.lower(),
			parts.netloc.lower(),
			parts.path,
			parts.query,
			"",
		)
	)
