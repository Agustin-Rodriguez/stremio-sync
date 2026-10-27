"""The tray app's worker: watch Stremio's cache, stage new movies and episodes, verify, send and import them."""

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .cache import MOVIE, CacheEntry, Skipped, read_info, scan_folder
from .config import Config
from .pipeline import fmt_duration, import_all, send_one
from .progress import CHECKING, DAMAGED, DOWNLOADING, IMPORTING, UPLOADING, WAITING, Progress
from .staging import Staging, StagingError, default_staging_dir
from .state import SentLog
from .verify import verify

log = logging.getLogger(__name__)

DEBOUNCE = 5              # seconds of quiet before looking at a changed folder (Stremio writes constantly)
FULL_SCAN_EVERY = 600     # in case a change notification was missed
SEND_RETRY_AFTER = 300    # after a failed upload, the server is probably down

# Modes, set from the tray menu.
RUNNING, PAUSE_SENDING, STOPPED = "running", "pause_sending", "stopped"
# Extra states reported to the tray, for its icon.
BUSY, SENDING, ERROR = "busy", "sending", "error"


class _Handler(FileSystemEventHandler):
    def __init__(self, engine: "Engine"):
        self.engine = engine

    def on_any_event(self, event):
        for path in (event.src_path, event.dest_path):
            if path:
                self.engine.changed(path)


class Engine:
    def __init__(self, cfg: Config, on_status: Callable[[str, str], None]):
        self.cfg = cfg
        self.on_status = on_status
        self.staging = Staging(cfg.staging_dir or default_staging_dir(cfg.cache_dir))
        self.sent = SentLog(cfg.state_file)
        self.mode = RUNNING
        self.status = (RUNNING, "Starting")
        self._ready: dict[str, CacheEntry] = {}   # verified, waiting to be sent
        self._bad: dict[str, float] = {}          # key -> data file mtime when it failed verification
        self._dirty: set[str] = set()
        self._last_change = 0.0
        self._full_scan = self._retry_import = True
        self._send_after = 0.0
        self._error: str | None = None
        self._rows = Progress()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._quit = threading.Event()
        self._observer = None
        self._thread = threading.Thread(target=self._loop, name="engine", daemon=True)

    # --- called from the tray (any thread) ---

    def start(self):
        self._start_watching()
        self._thread.start()

    def stop(self):
        """Quit after the current step. Blocks until the worker has exited."""
        self._quit.set()
        self._wake.set()
        self._stop_watching()
        self._thread.join()

    def set_mode(self, mode: str):
        if mode == self.mode:
            return
        log.info("\nMode: %s", mode.replace("_", " "))
        if mode == STOPPED:
            self._stop_watching()
        elif self.mode == STOPPED:
            self._start_watching()
            self._full_scan = True
        self.mode = mode
        self._wake.set()

    def sync_now(self):
        self._full_scan = self._retry_import = True
        self._send_after = 0.0
        self._error = None
        self._wake.set()

    def changed(self, path: str):
        path = Path(path)
        if path.is_relative_to(self.staging.root):
            return  # our own writes, if staging is set inside the cache folder
        try:
            rel = path.relative_to(self.cfg.cache_dir)
        except ValueError:
            return
        if rel.parts:
            with self._lock:
                self._dirty.add(rel.parts[0])
                self._last_change = time.monotonic()
            self._wake.set()

    def progress(self) -> list[dict]:
        """Rows for the window's In progress tab. Safe to call from any thread."""
        return self._rows.snapshot(self._waiting_reason())

    def _waiting_reason(self) -> str:
        if not self.cfg.ssh_target:
            return "no server set"
        if self.mode != RUNNING:
            return "paused" if self.mode == PAUSE_SENDING else "stopped"
        left = self._send_after - time.monotonic()
        return f"retrying in {fmt_duration(left)}" if left > 0 else "queued"

    # --- watching ---

    def _start_watching(self):
        try:
            observer = Observer()
            observer.schedule(_Handler(self), str(self.cfg.cache_dir), recursive=True)
            observer.start()
        except OSError as e:
            log.error("Can't watch %s: %s", self.cfg.cache_dir, e)
            self._error = "Can't watch the cache folder"
            return
        self._observer = observer

    def _stop_watching(self):
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None

    # --- the worker thread ---

    def _loop(self):
        next_full = 0.0
        while not self._quit.is_set():
            self._wake.clear()
            if self.mode != STOPPED and time.monotonic() >= next_full:
                self._full_scan = True
            names = self._take_changes()
            full = self._full_scan and self.mode != STOPPED
            if full:
                self._full_scan = False
                next_full = time.monotonic() + FULL_SCAN_EVERY
                names |= self._all_names()
            try:
                problems = self._pass(names)
            except Exception:  # keep the tray alive; the next pass may well work
                log.exception("Unexpected error")
                problems = 1
            self._report(problems, full)
            self._wake.wait(self._timeout(next_full))

    def _take_changes(self) -> set[str]:
        with self._lock:
            if not self._dirty or time.monotonic() - self._last_change < DEBOUNCE:
                return set()
            names, self._dirty = self._dirty, set()
            return names

    def _timeout(self, next_full: float) -> float:
        now = time.monotonic()
        with self._lock:
            if self._dirty:
                return max(DEBOUNCE - (now - self._last_change), 0.1)
        return max(next_full - now, 1) if self.mode != STOPPED else None

    def _all_names(self) -> set[str]:
        names = {p.name for p in self.cfg.cache_dir.iterdir() if p.is_dir()}
        return names | {e.infohash for e in self.staging.entries()}

    def _pass(self, names: set[str]) -> int:
        """Check changed folders, then send and import what's ready. Returns the number of problems."""
        problems = 0
        for name in sorted(names):
            if self._quit.is_set():
                return problems
            problems += self._check(name)
        if self.mode == RUNNING and not self._quit.is_set():
            failures, sent_any = self._send_ready()
            problems += failures
            if sent_any or self._retry_import:
                self._retry_import = False
                problems += self._import_and_clean()
        return problems

    def _check(self, name: str) -> int:
        """Stage and check one cache folder's movie or episodes. Returns the number of problems."""
        src = self.cfg.cache_dir / name
        found = scan_folder(src) if src.is_dir() else []
        if isinstance(found, Skipped):
            log.debug("%7s  %s  %s", "skip", found.folder[:12], found.reason)
            found = []
        items = {e.key: e for e in found if self.cfg.dest_for(e) is not None}
        problems = 0
        for item in items.values():
            if item not in self.sent:
                try:
                    self.staging.protect(item)
                except StagingError as e:
                    log.error("%s", e)
                    problems += 1
        staged = [e for e in self.staging.entries_in(name) if self.cfg.dest_for(e) is not None]
        self._rows.untrack_folder(name, keep={e.key for e in staged})
        for entry in staged:
            problems += self._check_entry(entry, items.get(entry.key))
        return problems

    def _check_entry(self, staged: CacheEntry, item: CacheEntry | None) -> int:
        """`item` is the same file in Stremio's cache, None if Stremio dropped it."""
        if staged.key in self._ready:
            return 0
        if staged in self.sent:
            self._rows.track(staged, IMPORTING)
            return 0
        if item is not None and not item.complete:
            self._track_download(item)
            return 0
        return self._verify(staged, source_gone=item is None)

    def _track_download(self, item: CacheEntry):
        try:
            mtime = item.src.stat().st_mtime
        except OSError:
            mtime = time.time()
        self._rows.track(item, DOWNLOADING, have=item.have, total=item.total, mtime=mtime)

    def _verify(self, entry: CacheEntry, source_gone: bool) -> int:
        try:
            mtime = entry.src.stat().st_mtime
        except OSError:
            return 0
        if not source_gone and self._bad.get(entry.key) == mtime:
            return 0  # already failed, and Stremio hasn't written to it since
        self._set_status(BUSY, f"Checking {entry.name}")
        self._rows.track(entry, CHECKING)
        try:
            ok = verify(entry, read_info(entry.src.parent))
        except ValueError as e:
            log.debug("Can't verify %s (%s), trusting Stremio's bitfield", entry.name, e)
            ok = entry.complete
        except OSError as e:
            log.error("Can't read %s: %s", entry.src, e)
            self._rows.untrack(entry)
            return 1
        if ok:
            log.info("\nReady: %s", entry.name)
            self._ready[entry.key] = entry
            self._rows.track(entry, WAITING)
            self._bad.pop(entry.key, None)
            return 0
        if source_gone:
            log.info("\nDropped %s: Stremio removed it before it finished", entry.name)
            self.staging.drop(entry)
            self._rows.untrack(entry)
            return 0
        self._bad[entry.key] = mtime
        self._rows.track(entry, DAMAGED)
        log.warning("\n%s: Stremio says it's complete but pieces don't match, waiting", entry.name)
        return 1

    def _send_ready(self) -> tuple[int, bool]:
        """Send verified entries. Returns (failures, whether anything was sent)."""
        if not self._ready or time.monotonic() < self._send_after:
            return 0, False
        if not self.cfg.ssh_target:
            log.error("No server set ([server] ssh in config.toml), can't send.")
            return 1, False
        entries, sent_any = list(self._ready.values()), False
        for n, entry in enumerate(entries, 1):
            if self._quit.is_set() or self.mode != RUNNING:
                break
            self._set_status(SENDING, f"Sending {entry.name} ({n}/{len(entries)})")
            log.info("\n[%d/%d] %s  (%.2f GB)", n, len(entries), entry.name, entry.size / 1e9)
            self._rows.track(entry, UPLOADING, started=time.monotonic())
            if send_one(entry, self.cfg, self.sent) is None:
                self._send_after = time.monotonic() + SEND_RETRY_AFTER
                self._rows.track(entry, WAITING)
                return 1, sent_any
            del self._ready[entry.key]
            self._rows.track(entry, IMPORTING)
            sent_any = True
        return 0, sent_any

    def _import_and_clean(self) -> int:
        """Run the Radarr/Sonarr imports, then drop staged copies that are imported (or sent, with no importer)."""
        waiting = [e for e in self.staging.entries() if e in self.sent]
        if not waiting:
            return 0
        problems = 0
        kinds = {e.kind for e in waiting if self.cfg.arr_for(e) is not None and not self.sent.is_imported(e)}
        if kinds:
            names = " and ".join(sorted("Radarr" if k == MOVIE else "Sonarr" for k in kinds))
            self._set_status(BUSY, f"Importing into {names}")
            problems = import_all(self.cfg, self.sent, kinds)
        for e in waiting:
            if self.cfg.arr_for(e) is None or self.sent.is_imported(e):
                log.debug("Unstaged %s", e.name)
                self.staging.drop(e)
                self._rows.untrack(e)
        return problems

    # --- status ---

    def _report(self, problems: int, full: bool):
        if problems:
            self._error = "Something went wrong, see the log"
        elif full:
            self._error = None  # a clean full scan clears the last error
        if self._error:
            self._set_status(ERROR, self._error)
        elif self.mode == STOPPED:
            self._set_status(STOPPED, "Stopped watching")
        elif self.mode == PAUSE_SENDING:
            self._set_status(PAUSE_SENDING, f"Sending paused, {len(self._ready)} file(s) waiting")
        else:
            self._set_status(RUNNING, "Watching the cache")

    def _set_status(self, state: str, text: str):
        if (state, text) != self.status:
            self.status = (state, text)
            self.on_status(state, text)
