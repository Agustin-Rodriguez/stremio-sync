"""sent.json: which cache entries have already been sent to the server."""

import json
import os
from datetime import datetime
from pathlib import Path

from .cache import CacheEntry


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class SentLog:
    def __init__(self, path: Path):
        self.path = path
        try:
            self._data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._data = {}

    def __contains__(self, entry: CacheEntry) -> bool:
        return entry.key in self._data

    def is_imported(self, entry: CacheEntry) -> bool:
        return bool(self._data.get(entry.key, {}).get("imported_at"))

    def records(self) -> list[dict]:
        return list(self._data.values())

    def mark(self, entry: CacheEntry):
        self._data[entry.key] = {
            "name": entry.name,
            "size": entry.size,
            "kind": entry.kind,
            "sent_at": now(),
        }
        self._save()

    def mark_imported(self, names: set[str]):
        """Record that Radarr or Sonarr imported these files (matched by file name)."""
        for rec in self._data.values():
            if rec.get("name") in names and not rec.get("imported_at"):
                rec["imported_at"] = now()
        self._save()

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic, so a crash never leaves a half-written file
