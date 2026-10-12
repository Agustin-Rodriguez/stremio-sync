"""sent.json: which cache entries have already been sent to the server."""

import json
import os
from datetime import datetime
from pathlib import Path

from .cache import CacheEntry


class SentLog:
    def __init__(self, path: Path):
        self.path = path
        try:
            self._data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._data = {}

    def __contains__(self, entry: CacheEntry) -> bool:
        return entry.key in self._data

    def mark(self, entry: CacheEntry):
        self._data[entry.key] = {
            "name": entry.name,
            "sent_at": datetime.now().isoformat(timespec="seconds"),
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic, so a crash never leaves a half-written file
