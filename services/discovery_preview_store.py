"""Signed, expiring file-backed storage for discovery previews."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
from typing import Any

from itsdangerous import BadData, URLSafeTimedSerializer

from discovery.models import DiscoveryResult


_STATE_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")


class DiscoveryPreviewError(RuntimeError):
	"""Raised when a preview token is invalid, expired, or unavailable."""


class DiscoveryPreviewStore:
	"""Keep bounded result payloads off the Flask cookie session."""

	def __init__(self, state_dir: str | Path, secret_key: str, ttl_seconds: int = 1800) -> None:
		self.state_dir = Path(state_dir)
		self.ttl_seconds = int(ttl_seconds)
		self.serializer = URLSafeTimedSerializer(secret_key, salt="brand-discovery-preview")

	def save(self, result: DiscoveryResult) -> str:
		self.state_dir.mkdir(parents=True, exist_ok=True)
		self._remove_expired_files()
		state_id = secrets.token_hex(16)
		payload = {
			"created_at": datetime.now(timezone.utc).isoformat(),
			"result": result.to_dict(),
		}
		path = self._path_for(state_id)
		temporary_path = path.with_suffix(".tmp")
		temporary_path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
		temporary_path.replace(path)
		return self.serializer.dumps({"state_id": state_id})

	def load(self, token: str) -> DiscoveryResult:
		if not token:
			raise DiscoveryPreviewError("The discovery preview is missing. Run discovery again.")
		try:
			data = self.serializer.loads(token, max_age=self.ttl_seconds)
		except BadData as error:
			raise DiscoveryPreviewError("The discovery preview expired or is invalid. Run discovery again.") from error

		state_id = data.get("state_id") if isinstance(data, dict) else None
		if not isinstance(state_id, str) or _STATE_ID_PATTERN.fullmatch(state_id) is None:
			raise DiscoveryPreviewError("The discovery preview reference is invalid.")
		path = self._path_for(state_id)
		try:
			payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
		except (OSError, ValueError, TypeError) as error:
			raise DiscoveryPreviewError("The discovery preview is no longer available. Run discovery again.") from error
		return DiscoveryResult.from_dict(payload["result"])

	def _path_for(self, state_id: str) -> Path:
		return self.state_dir / f"{state_id}.json"

	def _remove_expired_files(self) -> None:
		cutoff = datetime.now(timezone.utc).timestamp() - self.ttl_seconds
		for path in self.state_dir.glob("*.json"):
			try:
				if path.stat().st_mtime < cutoff:
					path.unlink()
			except OSError:
				continue
