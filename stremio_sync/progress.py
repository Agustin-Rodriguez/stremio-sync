"""What the tray worker is doing with each movie, for the window's In progress tab."""

import threading
import time

from .cache import CacheEntry
from .pipeline import fmt_duration

STALLED_AFTER = 600  # seconds since Stremio last wrote to a partial download

# Also the table order: what's happening right now comes first.
UPLOADING, CHECKING, WAITING, DAMAGED, DOWNLOADING, IMPORTING = range(6)
LABELS = {CHECKING: "Checking", DAMAGED: "Damaged, waiting for Stremio", IMPORTING: "Waiting for Radarr"}


class Progress:
    """One row per torrent, keyed by infohash. The engine thread writes, the tk thread reads."""

    def __init__(self):
        self._lock = threading.Lock()
        self._rows: dict[str, dict] = {}

    def track(self, entry: CacheEntry, state: int, **extra):
        with self._lock:
            self._rows[entry.infohash] = {"name": entry.name, "size": entry.size, "state": state, **extra}

    def untrack(self, infohash: str):
        with self._lock:
            self._rows.pop(infohash, None)

    def snapshot(self, waiting_reason: str) -> list[dict]:
        with self._lock:
            rows = [{"key": k, **r} for k, r in self._rows.items()]
        rows.sort(key=lambda r: (r["state"], r["name"].lower()))
        return [{"key": r["key"], "name": r["name"], "size": r["size"], **_shown(r, waiting_reason)}
                for r in rows]


def _shown(row: dict, waiting_reason: str) -> dict:
    """The State and Progress columns, worked out now so timers tick between engine updates."""
    state = row["state"]
    if state == DOWNLOADING:
        pct = f"{row['have'] * 100 // row['total']}%"  # rounded down, so 99.7% doesn't read as done
        stalled = time.time() - row["mtime"] > STALLED_AFTER
        return {"state": "Stalled" if stalled else "Downloading", "progress": pct}
    if state == UPLOADING:
        return {"state": "Sending", "progress": fmt_duration(time.monotonic() - row["started"])}
    if state == WAITING:
        return {"state": f"Waiting to send ({waiting_reason})", "progress": ""}
    return {"state": LABELS[state], "progress": ""}
