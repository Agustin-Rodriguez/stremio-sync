"""The HTTP API of Stremio's streaming server, the part of the app that downloads torrents."""

import json
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT = 30          # per request; opening a file can wait for peers, so it gets longer
OPEN_TIMEOUT = 120


class StremioError(Exception):
    pass


class ServerDown(StremioError):
    """Stremio isn't running (its server refuses connections)."""


class StremioServer:
    def __init__(self, url: str):
        self.url = url.rstrip("/")

    def create(self, infohash: str, trackers: tuple[str, ...] = ()) -> dict:
        """Start the torrent (or get the running one). Its `files` appear once the info arrives."""
        sources = [f"dht:{infohash}"] + [f"tracker:{t}" for t in trackers]
        return self._json(f"/{infohash}/create", {
            "torrent": {"infoHash": infohash},
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
            raise StremioError(f"unexpected reply from Stremio for {path}") from e

    def _open(self, path: str, body: dict | None = None, timeout: float = TIMEOUT):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        req = urllib.request.Request(self.url + path, data=data, headers=headers)
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            raise StremioError(f"Stremio answered {e.code} for {path}") from e
        except urllib.error.URLError as e:
            if isinstance(e.reason, ConnectionError):
                raise ServerDown(f"can't reach Stremio at {self.url}") from e
            raise StremioError(f"Stremio didn't answer ({e.reason})") from e
        except ConnectionError as e:
            raise ServerDown(f"can't reach Stremio at {self.url}") from e
        except OSError as e:  # timeouts
            raise StremioError(f"Stremio didn't answer ({e})") from e
