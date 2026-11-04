"""Throwaway: checks how Stremio's streaming server behaves before we build the Add tab. Not committed.

Run from the repo folder on Windows, with Stremio Service (or Stremio) running:
    py probe_stremio.py "<stream link, magnet or info hash>"
    py probe_stremio.py "<link>" --full      # also read the whole file, to check it ends up complete

Prints as it goes and writes everything to probe-report.txt. Send me that file.
"""

import argparse
import base64
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stremio_sync.cache import default_cache_dir, scan_folder, video_files  # noqa: E402

REPORT = Path(__file__).resolve().parent / "probe-report.txt"
_out = REPORT.open("w", encoding="utf-8")


def say(*parts):
    line = " ".join(str(p) for p in parts)
    print(line, flush=True)
    _out.write(line + "\n")
    _out.flush()


def dump(title, data, limit=4000):
    text = json.dumps(data, indent=2, default=str)
    say(f"--- {title} ---")
    say(text if len(text) <= limit else text[:limit] + f"\n... ({len(text)} chars, cut)")


# --- parsing the pasted link ---

def parse_link(text):
    text = text.strip()
    m = re.match(r"^https?://[^/]+/([0-9a-fA-F]{40})/(\d+)", text)
    if m:
        return m[1].lower(), int(m[2]), []
    if text.lower().startswith("magnet:?"):
        params = urllib.parse.parse_qs(text[8:])
        for xt in params.get("xt", []):
            if xt.lower().startswith("urn:btih:"):
                h = xt[9:]
                h = h.lower() if len(h) == 40 else base64.b32decode(h.upper()).hex()
                return h, None, params.get("tr", [])
    if re.fullmatch(r"[0-9a-fA-F]{40}", text):
        return text.lower(), None, []
    sys.exit("Not a stream link, magnet or info hash")


# --- HTTP ---

class Server:
    def __init__(self, url):
        self.url = url.rstrip("/")

    def request(self, path, body=None, headers=None, timeout=30):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, headers=headers or {})
        if data is not None:
            req.add_header("Content-Type", "application/json")
        return urllib.request.urlopen(req, timeout=timeout)

    def json(self, path, body=None):
        with self.request(path, body) as resp:
            raw = resp.read()
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return {"_not_json": raw[:500].decode("utf-8", "replace")}


def engine_summary(stats):
    """The fields that look useful for progress and for telling what's streaming."""
    if not isinstance(stats, dict):
        return stats
    keys = ("name", "downloaded", "uploaded", "downloadSpeed", "uploadSpeed", "peers", "unchoked",
            "queued", "unique", "connectionTries", "swarmPaused", "swarmConnections", "swarmSize",
            "selections", "wires", "streamLen", "streamProgress", "streamName")
    out = {k: stats[k] for k in keys if k in stats}
    out["_all_keys"] = sorted(stats)
    return out


def all_engines(stats):
    if isinstance(stats, dict):
        return stats.items()
    if isinstance(stats, list):
        return ((s.get("infoHash", "?"), s) for s in stats if isinstance(s, dict))
    return []


# --- cache ---

def cache_state(cache_dir, infohash, idx):
    folder = cache_dir / infohash
    data = folder / str(idx)
    size = data.stat().st_size if data.is_file() else None
    found = scan_folder(folder) if folder.is_dir() else None
    entry = next((e for e in found if e.index == idx), None) if isinstance(found, list) else None
    if entry is None:
        why = found.reason if found is not None and not isinstance(found, list) else "no entry"
        return f"data file size={size}, scan: {why}"
    return f"data file size={size}, pieces {entry.have}/{entry.total}, complete={entry.complete}"


# --- the checks ---

def check_server(srv):
    say("\n== 1. Is the server up? ==")
    try:
        stats = srv.json("/stats.json")
    except (urllib.error.URLError, OSError) as e:
        sys.exit(f"Can't reach {srv.url}: {e}. Is Stremio Service running?")
    say("type of /stats.json:", type(stats).__name__, "engines:", len(stats) if hasattr(stats, "__len__") else "?")
    for h, s in all_engines(stats):
        dump(f"engine {h}", engine_summary(s))
    try:
        dump("/settings", srv.json("/settings"), limit=2500)
    except (urllib.error.URLError, OSError) as e:
        say("/settings failed:", e)


def create(srv, infohash, trackers):
    say("\n== 2. Create the torrent ==")
    sources = [f"dht:{infohash}"] + [f"tracker:{t}" for t in trackers]
    body = {"torrent": {"infoHash": infohash}, "peerSearch": {"sources": sources, "min": 40, "max": 150}}
    for method, payload in (("POST", body), ("GET", None)):
        try:
            started = time.monotonic()
            reply = srv.json(f"/{infohash}/create", payload)
            say(f"{method} /create OK in {time.monotonic() - started:.1f}s")
            dump("create reply", engine_summary(reply) if isinstance(reply, dict) else reply)
            if isinstance(reply, dict) and reply.get("files"):
                dump("create reply files", reply["files"][:20])
            return
        except (urllib.error.URLError, OSError) as e:
            say(f"{method} /create failed: {e}")
    sys.exit("Couldn't create the torrent")


def wait_files(srv, infohash, timeout=300):
    say("\n== 3. Wait for the torrent's info (file list) ==")
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        try:
            stats = srv.json(f"/{infohash}/stats.json")
        except (urllib.error.URLError, OSError) as e:
            stats = None
            say("stats.json failed:", e)
        if isinstance(stats, dict) and stats.get("files"):
            say(f"Got the file list after {time.monotonic() - started:.0f}s")
            dump("engine stats", engine_summary(stats))
            dump("files (first 20)", stats["files"][:20])
            return stats
        time.sleep(2)
    sys.exit("No file list after 5 minutes (no peers?)")


def pick(stats, idx):
    files = [(f.get("path") or f.get("name"), f["length"]) for f in stats["files"]]
    if idx is None:
        videos = video_files(files)
        if not videos:
            sys.exit("No video file in the torrent")
        idx = max(videos, key=lambda v: v[2])[0]
    say(f"\nUsing file #{idx}: {files[idx][0]} ({files[idx][1] / 1e9:.2f} GB)")
    return idx, files[idx][1]


def check_file_stats(srv, infohash, idx):
    say("\n== 4. Per-file stats ==")
    try:
        dump(f"/{infohash}/{idx}/stats.json", engine_summary(srv.json(f"/{infohash}/{idx}/stats.json")))
    except (urllib.error.URLError, OSError) as e:
        say("failed:", e)


def check_range(srv, infohash, idx):
    say("\n== 5. Does the stream support Range (to resume mid-file)? ==")
    try:
        with srv.request(f"/{infohash}/{idx}", headers={"Range": "bytes=1000-"}, timeout=120) as resp:
            say("status:", resp.status, "Content-Range:", resp.headers.get("Content-Range"),
                "Content-Length:", resp.headers.get("Content-Length"))
    except (urllib.error.URLError, OSError) as e:
        say("failed:", e)


def engine_downloaded(srv, infohash):
    try:
        s = srv.json(f"/{infohash}/stats.json")
        return s.get("downloaded"), s.get("downloadSpeed"), s.get("peers")
    except (urllib.error.URLError, OSError, AttributeError):
        return None, None, None


def read_stream(srv, infohash, idx, length, cache_dir, seconds, full):
    what = "the whole file" if full else f"{seconds}s"
    say(f"\n== 6. Read the stream for {what}, throwing the bytes away ==")
    got, started, last = 0, time.monotonic(), 0.0
    try:
        with srv.request(f"/{infohash}/{idx}", timeout=60) as resp:
            say("status:", resp.status, "Content-Length:", resp.headers.get("Content-Length"))
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    say("stream ended")
                    break
                got += len(chunk)
                now = time.monotonic()
                if now - last >= 5:
                    last = now
                    dl, speed, peers = engine_downloaded(srv, infohash)
                    say(f"  {now - started:5.0f}s read={got / 1e6:8.1f} MB ({got * 100 / length:5.1f}%) "
                        f"our speed={got / (now - started) / 1e6:5.2f} MB/s | engine downloaded={dl} "
                        f"speed={speed} peers={peers}")
                if not full and now - started >= seconds:
                    break
    except (urllib.error.URLError, OSError) as e:
        say(f"read failed after {got / 1e6:.1f} MB: {e}")
    say("cache:", cache_state(cache_dir, infohash, idx))
    return got


def after_stop(srv, infohash, idx, cache_dir, seconds):
    say(f"\n== 7. Stopped reading. Does the server keep downloading? (watching {seconds}s) ==")
    for _ in range(seconds // 5):
        dl, speed, peers = engine_downloaded(srv, infohash)
        say(f"  engine downloaded={dl} speed={speed} peers={peers} | {cache_state(cache_dir, infohash, idx)}")
        time.sleep(5)
    try:
        stats = srv.json("/stats.json")
        say("still listed in /stats.json:", any(h == infohash for h, _ in all_engines(stats)))
    except (urllib.error.URLError, OSError) as e:
        say("/stats.json failed:", e)


def while_watching(srv, infohash):
    say("\n== 8. What does /stats.json show while you watch something else? ==")
    try:
        input("Play something ELSE in Stremio now (a different movie). Once it's playing, press Enter here... ")
    except EOFError:
        say("skipped (no console input)")
        return
    for n in range(3):
        stats = srv.json("/stats.json")
        say(f"sample {n + 1}:")
        for h, s in all_engines(stats):
            mark = "  (ours)" if h == infohash else ""
            dump(f"engine {h}{mark}", engine_summary(s))
        time.sleep(5)
    try:
        input("Now stop playing (close the player). Press Enter... ")
    except EOFError:
        return
    time.sleep(10)
    for h, s in all_engines(srv.json("/stats.json")):
        dump(f"after stopping: engine {h}", engine_summary(s))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("link")
    ap.add_argument("--server", default="http://127.0.0.1:11470")
    ap.add_argument("--cache-dir", type=Path, default=None)
    ap.add_argument("--seconds", type=int, default=60, help="how long to read, then how long to watch after")
    ap.add_argument("--full", action="store_true", help="read the whole file instead of --seconds")
    ap.add_argument("--skip-watching", action="store_true", help="skip the play-something-else check")
    args = ap.parse_args()

    srv = Server(args.server)
    cache_dir = args.cache_dir or default_cache_dir()
    infohash, idx, trackers = parse_link(args.link)
    say("server:", srv.url, "| cache:", cache_dir, "| hash:", infohash, "| file:", idx, "| trackers:", len(trackers))

    check_server(srv)
    create(srv, infohash, trackers)
    stats = wait_files(srv, infohash)
    idx, length = pick(stats, idx)
    check_file_stats(srv, infohash, idx)
    check_range(srv, infohash, idx)
    read_stream(srv, infohash, idx, length, cache_dir, args.seconds, args.full)
    after_stop(srv, infohash, idx, cache_dir, args.seconds)
    if not args.skip_watching:
        while_watching(srv, infohash)
    say(f"\nDone. Report: {REPORT}")


if __name__ == "__main__":
    main()
