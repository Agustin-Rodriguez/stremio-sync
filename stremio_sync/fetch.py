"""Pasted stream links and magnets: Stremio's server downloads them into its cache, no player needed."""

import base64
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .cache import EPISODE, pick_files, scan_folder
from .config import Config
from .state import now

log = logging.getLogger(__name__)

CHECK_EVERY = 5       # seconds between looks at each download
INFO_TIMEOUT = 600    # the torrent's info must arrive within this long, counting only time Stremio is up
STALLED_AFTER = 300   # no progress for this long: say so
REOPEN_AFTER = 600    # and for this long: open the file again, in case Stremio dropped it
TIMEOUT = 30          # per request; opening a file can wait for peers, so it gets longer
OPEN_TIMEOUT = 120

# Saved states. What the Add tab shows is worked out from these plus what's happening now.
QUEUED, INFO, DOWNLOADING, DONE, FAILED = "queued", "info", "downloading", "done", "failed"
FINISHED = (DONE, FAILED)
SAVED = ("id", "link", "infohash", "index", "name", "size", "kind", "files", "done_files",
         "state", "error", "added_at")

HEX_HASH = re.compile(r"[0-9a-fA-F]{40}")
STREAM_LINK = re.compile(r"^https?://[^/]+/([0-9a-fA-F]{40})/(\d+)(?:[/?#]|$)")


class FetchError(Exception):
    pass


class ServerDown(FetchError):
    """Stremio isn't running (its server refuses connections)."""


class Unsendable(FetchError):
    """The torrent has nothing stremio-sync would send, so downloading it is pointless."""


@dataclass(frozen=True)
class Link:
    infohash: str
    index: int | None              # file within the torrent; None = work it out from the file list
    trackers: tuple[str, ...] = ()
    name: str | None = None        # a magnet's dn, until the torrent's info arrives


def parse_link(text: str) -> Link:
    """A Stremio stream link, a magnet or a bare info hash. Raises FetchError."""
    text = text.strip()
    match = STREAM_LINK.match(text)
    if match:
        return Link(match[1].lower(), int(match[2]))
    if text.lower().startswith("magnet:?"):
        params = urllib.parse.parse_qs(text[len("magnet:?"):])
        for xt in params.get("xt", []):
            if xt.lower().startswith("urn:btih:"):
                return Link(_hex_hash(xt[len("urn:btih:"):]), None, tuple(params.get("tr", [])),
                            params.get("dn", [None])[0])
    if HEX_HASH.fullmatch(text):
        return Link(text.lower(), None)
    raise FetchError("not a Stremio stream link or magnet")


def _hex_hash(h: str) -> str:
    if HEX_HASH.fullmatch(h):
        return h.lower()
    if re.fullmatch(r"[A-Za-z2-7]{32}", h):
        return base64.b32decode(h.upper()).hex()
    raise FetchError("the magnet's info hash is unreadable")


def choose_files(link: Link, files: list[tuple[str, int]], series_on: bool) -> tuple[str, list[tuple[int, str, int]]]:
    """(kind, videos to download), by the same rules as the cache scan. Raises Unsendable."""
    picked = pick_files(files)
    if isinstance(picked, str):
        raise Unsendable(picked)
    kind, videos = picked
    if kind == EPISODE and not series_on:
        raise Unsendable("series syncing is off ([server] series_incoming)")
    if link.index is not None:
        videos = [v for v in videos if v[0] == link.index]
        if not videos:
            raise Unsendable("that file isn't a movie or episode stremio-sync sends")
    return kind, videos


class StremioServer:
    """The HTTP API of Stremio's streaming server, the part of the app that downloads torrents."""

    def __init__(self, url: str):
        self.url = url.rstrip("/")

    def create(self, link: Link) -> dict:
        """Start the torrent (or get the running one). Its `files` appear once the info arrives."""
        sources = [f"dht:{link.infohash}"] + [f"tracker:{t}" for t in link.trackers]
        return self._json(f"/{link.infohash}/create", {
            "torrent": {"infoHash": link.infohash},
            "peerSearch": {"sources": sources, "min": 40, "max": 150},
        })

    def select(self, infohash: str, idx: int):
        """Open the file and read a little. From then on Stremio downloads all of it by itself."""
        with self._open(f"/{infohash}/{idx}", timeout=OPEN_TIMEOUT) as resp:
            resp.read(1 << 16)

    def progress(self, infohash: str, idx: int) -> float:
        """How much of the file is downloaded, 0 to 1."""
        return float(self._json(f"/{infohash}/{idx}/stats.json").get("streamProgress") or 0)

    def active(self) -> set[str]:
        """Info hashes of the torrents the server is running."""
        stats = self._json("/stats.json")
        return set(stats) if isinstance(stats, dict) else set()

    def cache_root(self) -> Path | None:
        root = self._json("/settings").get("values", {}).get("cacheRoot")
        return Path(root) if root else None

    def _json(self, path: str, body: dict | None = None):
        with self._open(path, body) as resp:
            raw = resp.read()
        try:
            return json.loads(raw) if raw else {}
        except ValueError as e:
            raise FetchError(f"unexpected reply from Stremio for {path}") from e

    def _open(self, path: str, body: dict | None = None, timeout: float = TIMEOUT):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        req = urllib.request.Request(self.url + path, data=data, headers=headers)
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            raise FetchError(f"Stremio answered {e.code} for {path}") from e
        except urllib.error.URLError as e:
            if isinstance(e.reason, ConnectionError):
                raise ServerDown(f"can't reach Stremio at {self.url}") from e
            raise FetchError(f"Stremio didn't answer ({e.reason})") from e
        except ConnectionError as e:
            raise ServerDown(f"can't reach Stremio at {self.url}") from e
        except OSError as e:  # timeouts
            raise FetchError(f"Stremio didn't answer ({e})") from e


@dataclass
class _Watch:
    """A download thread's view of the file it's on. Not saved: a restart opens the file again."""
    index: int | None = None
    progress: float = 0.0
    moved: float = 0.0       # when progress last went up
    opened: float = 0.0      # when we last opened the file
    bytes: int = 0
    at: float = 0.0          # when `bytes` was measured, for the speed


class Fetcher:
    """Pasted links, saved to fetches.json. Up to `max_downloads` run at once, each on its own thread.

    A download survives Stremio closing (it waits, then opens the torrent again) and the tray app
    quitting (it's picked up from fetches.json on the next start).
    """

    def __init__(self, cfg: Config, path: Path, on_fetched: Callable[[str], None]):
        self.cfg = cfg
        self.path = path
        self.on_fetched = on_fetched  # called with the info hash when a file is complete
        self.server = StremioServer(cfg.stremio_server)
        self._lock = threading.Lock()
        self._recs: dict[str, dict] = {}              # id -> record, in the order they were added
        self._stops: dict[str, threading.Event] = {}  # the ones with a running thread
        self._held = threading.Event()                # the tray's Stop watching
        self._quit = threading.Event()
        self._root_checked = False
        self._load()

    # --- called from the tray and the window (any thread) ---

    def start(self):
        with self._lock:
            self._schedule()

    def stop(self):
        """Stop the download threads. They're daemons, so this doesn't wait; fetches.json is up to date."""
        self._quit.set()
        with self._lock:
            for stop in self._stops.values():
                stop.set()

    def hold(self, on: bool):
        if on:
            self._held.set()
        else:
            self._held.clear()

    def add(self, text: str):
        """Queue a pasted link. Raises FetchError if it can't be read or is already in the list."""
        link = parse_link(text)
        with self._lock:
            for r in self._recs.values():
                if r["state"] not in FINISHED and r["infohash"] == link.infohash and \
                        (r["index"] is None or link.index is None or r["index"] == link.index):
                    raise FetchError("already in the list")
            fid = uuid.uuid4().hex[:12]
            self._recs[fid] = {
                "id": fid, "link": text.strip(), "infohash": link.infohash, "index": link.index,
                "name": link.name or text.strip(), "size": None, "kind": None, "files": None,
                "done_files": [], "state": QUEUED, "error": None, "added_at": now(),
            }
            self._save()
            self._schedule()
        log.info("\nAdded: %s", link.name or text.strip()[:80])

    def cancel(self, fid: str):
        """Drop a download from the list (stopping it if it's running). What's in the cache stays."""
        with self._lock:
            rec = self._recs.pop(fid, None)
            if fid in self._stops:
                self._stops[fid].set()
            if rec is not None:
                self._save()
        if rec is not None and rec["state"] not in FINISHED:
            log.info("\nCancelled: %s", rec["name"])

    def clear_finished(self):
        with self._lock:
            for fid in [f for f, r in self._recs.items() if r["state"] in FINISHED]:
                del self._recs[fid]
            self._save()

    def snapshot(self) -> list[dict]:
        """Rows for the window's Add tab."""
        with self._lock:
            recs = [dict(r) for r in self._recs.values()]
        held = self._held.is_set()
        return [{"key": r["id"], "name": r["name"], "size": r["size"], "phase": r["state"],
                 **_shown(r, held)} for r in recs]

    # --- bookkeeping (call with the lock held) ---

    def _schedule(self):
        if self._quit.is_set():
            return
        for fid, rec in self._recs.items():
            if len(self._stops) >= self.cfg.max_downloads:
                break
            if rec["state"] not in FINISHED and fid not in self._stops:
                stop = self._stops[fid] = threading.Event()
                threading.Thread(target=self._run, args=(fid, stop), name=f"fetch-{fid}", daemon=True).start()

    def _load(self):
        try:
            recs = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            log.error("Can't read %s: %s", self.path, e)
            return
        for r in recs:
            if r.get("state") not in FINISHED:
                r["state"] = QUEUED if r.get("files") is None else DOWNLOADING
            self._recs[r["id"]] = r

    def _save(self):
        data = [{k: r.get(k) for k in SAVED} for r in self._recs.values()]
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)  # atomic, so a crash never leaves a half-written file
        except OSError as e:
            log.error("Can't save %s: %s", self.path, e)

    def _set(self, fid: str, save: bool = False, **fields) -> dict | None:
        """Update a record; returns a copy, or None if it was cancelled."""
        with self._lock:
            rec = self._recs.get(fid)
            if rec is None:
                return None
            rec.update(fields)
            if save:
                self._save()
            return dict(rec)

    # --- one download, on its own thread ---

    def _run(self, fid: str, stop: threading.Event):
        try:
            self._work(fid, stop)
        except Exception:  # keep the tray alive
            log.exception("Download %s crashed", fid)
            self._set(fid, save=True, state=FAILED, error="unexpected error, see the log")
        finally:
            with self._lock:
                self._stops.pop(fid, None)
                self._schedule()

    def _work(self, fid: str, stop: threading.Event):
        rec = self._set(fid)
        if rec is None:
            return
        link = parse_link(rec["link"])
        files = rec["files"]  # [[index, name, length], ...] once the torrent's info has arrived
        self._set(fid, state=INFO if files is None else DOWNLOADING)
        watch = _Watch()
        info_since = time.monotonic()
        while not stop.is_set():
            try:
                if self._held.is_set():
                    info_since = time.monotonic()
                elif files is None:
                    files = self._get_files(fid, link)
                    if files is None and time.monotonic() - info_since > INFO_TIMEOUT:
                        raise Unsendable(f"no torrent info after {INFO_TIMEOUT // 60} minutes (no peers?)")
                elif self._step(fid, link, files, watch):
                    self._set(fid, save=True, state=DONE)
                    return
                self._set(fid, down=False, note=None)
                self._check_cache_root()
            except Unsendable as e:
                log.warning("\nCan't download %s: %s", rec["name"], e)
                self._set(fid, save=True, state=FAILED, error=str(e))
                return
            except ServerDown:
                self._set(fid, down=True)
                info_since = time.monotonic()  # waiting for the app to open doesn't count
                watch.index = None             # open the file again once it's back
            except FetchError as e:
                log.debug("Download %s: %s", fid, e)
                self._set(fid, down=False, note=str(e))
            stop.wait(CHECK_EVERY)

    def _get_files(self, fid: str, link: Link) -> list | None:
        """Start the torrent and pick its files, or None while its info hasn't arrived."""
        stats = self.server.create(link)
        raw = stats.get("files")
        if not raw:
            return None
        try:
            files = [(f.get("path") or f["name"], int(f["length"])) for f in raw]
        except (KeyError, TypeError, ValueError) as e:
            raise FetchError("unexpected file list from Stremio") from e
        kind, videos = choose_files(link, files, self.cfg.server_series_incoming is not None)
        chosen = [[idx, path.split("/")[-1], length] for idx, path, length in videos]
        name = chosen[0][1] if len(chosen) == 1 else stats.get("name") or chosen[0][1]
        size = sum(f[2] for f in chosen)
        self._set(fid, save=True, files=chosen, name=name, kind=kind, size=size, state=DOWNLOADING)
        log.info("\nDownloading %s (%d file(s), %.2f GB)", name, len(chosen), size / 1e9)
        return chosen

    def _step(self, fid: str, link: Link, files: list, watch: _Watch) -> bool:
        """Check on the file being downloaded. True once every file is done."""
        rec = self._set(fid)
        if rec is None:
            return False
        done = set(rec["done_files"])
        todo = [f for f in files if f[0] not in done]
        if not todo:
            return True
        idx, name, length = todo[0]
        t = time.monotonic()
        if watch.index != idx or link.infohash not in self.server.active():
            # A new file, or Stremio was restarted and forgot the torrent. The cache keeps what's done.
            self.server.create(link)
            self._open_file(link.infohash, idx)
            watch.index, watch.progress, watch.moved, watch.opened, watch.bytes, watch.at = idx, 0.0, t, t, 0, t
        progress = self.server.progress(link.infohash, idx)
        if progress >= 1 or self._complete_in_cache(link.infohash, idx):
            self._set(fid, save=True, done_files=rec["done_files"] + [idx])
            log.info("\nDownloaded: %s", name)
            self.on_fetched(link.infohash)
            watch.index = None
            return len(todo) == 1
        if progress > watch.progress:
            watch.progress, watch.moved = progress, t
        elif t - max(watch.moved, watch.opened) > REOPEN_AFTER:
            log.debug("%s stalled, opening it again", name)
            self._open_file(link.infohash, idx)
            watch.opened = t
        got = int(progress * length)
        speed = (got - watch.bytes) / (t - watch.at) if t > watch.at else 0.0
        watch.bytes, watch.at = got, t
        self._set(fid, done_bytes=sum(f[2] for f in files if f[0] in done) + got,
                  part=(len(done) + 1, len(files)), speed=max(speed, 0.0),
                  stalled=t - watch.moved > STALLED_AFTER)
        return False

    def _open_file(self, infohash: str, idx: int):
        """In the background: opening can wait a while for peers, and the reply doesn't matter."""
        def run():
            try:
                self.server.select(infohash, idx)
            except FetchError as e:
                log.debug("Opening %s/%d: %s", infohash[:12], idx, e)
        threading.Thread(target=run, name="fetch-open", daemon=True).start()

    def _complete_in_cache(self, infohash: str, idx: int) -> bool:
        """What the engine goes by. Covers Stremio's progress never quite reaching 1."""
        folder = self.cfg.cache_dir / infohash
        try:
            found = scan_folder(folder) if folder.is_dir() else []
        except OSError:
            return False
        return isinstance(found, list) and any(e.index == idx and e.complete for e in found)

    def _check_cache_root(self):
        """Once: warn if Stremio downloads somewhere the engine isn't watching."""
        if self._root_checked:
            return
        try:
            root = self.server.cache_root()
        except FetchError:
            return
        self._root_checked = True
        if root is None:
            return
        theirs = os.path.normcase(os.path.normpath(root / "stremio-cache"))
        if theirs != os.path.normcase(os.path.normpath(self.cfg.cache_dir)):
            log.warning("\nStremio keeps its cache in %s, but stremio-sync watches %s. "
                        "Set [stremio] cache_dir in config.toml.", theirs, self.cfg.cache_dir)


def _shown(rec: dict, held: bool) -> dict:
    """The State and Progress columns."""
    state = rec["state"]
    if state == QUEUED:
        return {"state": "Queued", "progress": ""}
    if state == DONE:
        return {"state": "Done", "progress": "100%"}
    if state == FAILED:
        return {"state": f"Failed: {rec['error']}", "progress": ""}
    progress = ""
    if rec.get("done_bytes") is not None and rec.get("size"):
        progress = f"{rec['done_bytes'] * 100 // rec['size']}%"  # rounded down, so 99.7% doesn't read as done
        if rec.get("speed"):
            progress += f"  {rec['speed'] / 1e6:.1f} MB/s"
    if held:
        text = "Paused"
    elif rec.get("down"):
        text = "Waiting for Stremio to open"
    elif state == INFO:
        text = "Getting torrent info"
    elif rec.get("stalled"):
        text = "Stalled, no peers sending"
    else:
        n, total = rec.get("part") or (1, 1)
        text = f"Downloading episode {n} of {total}" if total > 1 else "Downloading"
    if rec.get("note") and not rec.get("down"):
        text += f" (retrying: {rec['note']})"
    return {"state": text, "progress": progress}
