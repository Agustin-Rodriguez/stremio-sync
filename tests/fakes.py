"""Stand-ins for the outside world: Radarr/Sonarr's HTTP API, Stremio's server, and ssh/scp."""

import threading
import types

from stremio_sync.clients.home_server import TransferError
from stremio_sync.clients.stremio_server import ServerDown


class FakeArrAPI:
    """Replaces ArrClient.request. Routes are {(method, path): value or fn(params, body)}."""

    def __init__(self, routes: dict | None = None):
        self.routes = dict(routes or {})
        self.calls: list[tuple[str, str, dict | None, object]] = []

    def install(self, monkeypatch):
        from stremio_sync.clients.arr import base, sonarr
        api = self

        def request(client, method, path, params=None, body=None):
            api.calls.append((method, path, params, body))
            if (method, path) not in api.routes:
                raise AssertionError(f"unexpected {method} {path} {params or ''}")
            handler = api.routes[(method, path)]
            if isinstance(handler, Exception):
                raise handler
            return handler(params, body) if callable(handler) else handler

        monkeypatch.setattr(base.ArrClient, "request", request)
        monkeypatch.setattr(base.time, "sleep", lambda _: None)
        monkeypatch.setattr(sonarr.time, "sleep", lambda _: None)
        return self

    def called(self, method, path) -> list:
        return [c for c in self.calls if c[:2] == (method, path)]


class FakeSend:
    """Replaces home_server.send (as imported into pipeline). Outcomes are popped per call; default 'sent'."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, entry, target, dest):
        self.calls.append((entry.key, target, dest))
        outcome = self.outcomes.pop(0) if self.outcomes else "sent"
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def install(self, monkeypatch):
        from stremio_sync import pipeline
        monkeypatch.setattr(pipeline, "send", self)
        return self


def transfer_error(msg="scp failed: connection refused"):
    return TransferError(msg)


class FakeImporter:
    """Replaces Radarr/Sonarr in pipeline.IMPORTERS: import_incoming returns a scripted result."""

    def __init__(self, result=(0, set()), error: Exception | None = None):
        self.result, self.error = result, error
        self.runs = 0

    def __call__(self, arr_cfg):  # constructed like Radarr(cfg)
        return self

    def import_incoming(self):
        self.runs += 1
        if self.error:
            raise self.error
        return self.result


class FakeStremio:
    """Replaces StremioServer. Torrents are {infohash: {"name", "files": [{path, name, length}], ...}}.

    `script` is a list of callables run one per progress() call, to change the world between steps
    (Stremio closing, progress moving). down=True makes every call raise ServerDown.
    """

    def __init__(self, torrents: dict, cache_root=None):
        self.torrents = torrents
        self.root = cache_root
        self.progress_of: dict[tuple[str, int], float] = {}
        self.running: set[str] = set()
        self.info_after: dict[str, int] = {}   # infohash -> create() calls before files show up
        self.down = False
        self.calls: list[tuple] = []
        self.opened = threading.Event()
        self.on_progress = None                # fn(infohash, idx) called before answering

    def _check(self):
        if self.down:
            raise ServerDown("can't reach Stremio")

    def create(self, infohash, trackers=()):
        self.calls.append(("create", infohash))
        self._check()
        self.running.add(infohash)
        t = self.torrents[infohash]
        left = self.info_after.get(infohash, 0)
        if left:
            self.info_after[infohash] = left - 1
            return {"infoHash": infohash}
        return {"infoHash": infohash, "name": t["name"], "files": t["files"]}

    def select(self, infohash, idx):
        self.calls.append(("select", infohash, idx))
        self.opened.set()

    def progress(self, infohash, idx):
        self.calls.append(("progress", infohash, idx))
        if self.on_progress:
            self.on_progress(infohash, idx)
        self._check()
        return self.progress_of.get((infohash, idx), 0.0)

    def active(self):
        self._check()
        return set(self.running)

    def cache_root(self):
        self._check()
        return self.root


def stremio_files(*files):
    """Stremio's create reply file list, from (path, length) pairs."""
    out, offset = [], 0
    for path, length in files:
        out.append({"path": path, "name": path.split("/")[-1], "length": length, "offset": offset})
        offset += length
    return out


class FakeClock:
    """Stands in for a module's `time` import: monotonic() and time() move only when told to."""

    def __init__(self, mono=1000.0, wall=1_700_000_000.0):
        self.mono, self.wall = mono, wall

    def monotonic(self):
        return self.mono

    def time(self):
        return self.wall

    def advance(self, secs):
        self.mono += secs
        self.wall += secs

    def sleep(self, secs):
        self.advance(secs)


class FakeThread:
    """Replaces threading.Thread in a module. Download threads are recorded, not started; any thread
    whose name is in `run_inline` runs right away on start(), so the test stays single-threaded."""

    started: list["FakeThread"]

    def __init__(self, target=None, args=(), name=None, daemon=None):
        self.target, self.args, self.name = target, args, name

    def start(self):
        type(self).started.append(self)
        if self.name in type(self).run_inline:
            self.target(*self.args)

    @classmethod
    def install(cls, monkeypatch, module, run_inline=("fetch-open",)):
        class Recorder(cls):
            started = []
        Recorder.run_inline = set(run_inline)
        fake_threading = types.SimpleNamespace(Thread=Recorder, Event=threading.Event, Lock=threading.Lock)
        monkeypatch.setattr(module, "threading", fake_threading)
        return Recorder


class ScriptedStop(threading.Event):
    """A download thread's stop event. Each wait() runs the next step; when the steps run out it sets
    itself, so the download loop always ends. A step returning "keep" is run again on the next wait."""

    def __init__(self, *steps):
        super().__init__()
        self.steps = list(steps)
        self.waits = 0

    def wait(self, timeout=None):
        self.waits += 1
        if self.waits > 500:
            raise AssertionError("download loop didn't finish")
        if not self.steps:
            self.set()
            return True
        if self.steps[0]() != "keep":
            self.steps.pop(0)
        return self.is_set()
