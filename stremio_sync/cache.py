"""Reading Stremio's cache: which movies are in it and how much of each is downloaded."""

import os
import platform
import re
from dataclasses import dataclass
from pathlib import Path

from .bencode import bdecode

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov"}
SERIES_PATTERN = re.compile(r"(s\d{1,2}e\d{1,2}|\d{1,2}x\d{2}|season\s?\d{1,2})", re.IGNORECASE)


@dataclass(frozen=True)
class CacheEntry:
    """The movie file inside one cached torrent."""
    infohash: str
    index: int    # file number within the torrent, which is also its file name in the cache folder
    src: Path
    name: str     # real file name, from the torrent metadata
    size: int
    have: int     # pieces downloaded, counting only the pieces that cover this file
    total: int

    @property
    def complete(self) -> bool:
        return self.have == self.total

    @property
    def key(self) -> str:
        """Stable ID for sent.json. Doesn't change if the cache folder moves."""
        return f"{self.infohash}:{self.index}"


@dataclass(frozen=True)
class Skipped:
    """A cache folder we couldn't use, and why."""
    folder: str
    reason: str


def default_cache_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        candidates = [
            Path(os.environ["LOCALAPPDATA"]) / "Stremio" / "stremio-cache",
            Path(os.environ["APPDATA"]) / "stremio" / "stremio-server" / "stremio-cache",
        ]
        return next((p for p in candidates if p.is_dir()), candidates[0])
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "stremio-server" / "stremio-cache"
    return Path.home() / ".stremio-server" / "stremio-cache"


def torrent_files(info: dict) -> list[tuple[str, int]]:
    """[(path, length), ...] in torrent order. A single-file torrent becomes a 1-item list."""
    if b"files" in info:
        return [
            (b"/".join(f[b"path"]).decode("utf-8", "replace"), f[b"length"])
            for f in info[b"files"]
        ]
    return [(info[b"name"].decode("utf-8", "replace"), info[b"length"])]


def looks_like_series(files: list[tuple[str, int]]) -> bool:
    """A season pack (multiple video files), or a filename carrying a season/episode marker."""
    video_files = [
        p for p, _ in files
        if Path(p).suffix.lower() in VIDEO_EXTS and "sample" not in p.lower()
    ]
    if len(video_files) > 1:
        return True
    return any(SERIES_PATTERN.search(p) for p in video_files)


def pick_movie(files):
    """Largest video file that isn't a sample: (index, path, length), or None."""
    candidates = [
        (idx, path, length)
        for idx, (path, length) in enumerate(files)
        if Path(path).suffix.lower() in VIDEO_EXTS and "sample" not in path.lower()
    ]
    return max(candidates, key=lambda c: c[2], default=None)


def count_have(bitfield: bytes, first: int, last: int) -> int:
    have = 0
    for p in range(first, last + 1):
        byte = p // 8
        if byte < len(bitfield) and (bitfield[byte] >> (7 - p % 8)) & 1:
            have += 1
    return have


def scan_entry(folder: Path) -> CacheEntry | Skipped:
    cache, bitfield_path = folder / "cache", folder / "bitfield"
    if not cache.is_file():
        return Skipped(folder.name, "no cache metadata")
    if not bitfield_path.is_file():
        return Skipped(folder.name, "no bitfield")

    try:
        meta = bdecode(cache.read_bytes())
        info = meta.get(b"info", meta)
        piece_len = info[b"piece length"]
        files = torrent_files(info)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as e:
        return Skipped(folder.name, f"unreadable metadata ({e!r})")

    if looks_like_series(files):
        return Skipped(folder.name, "looks like a series, skipping")

    movie = pick_movie(files)
    if movie is None:
        return Skipped(folder.name, "no video file in torrent")
    idx, path, length = movie

    data_file = folder / str(idx)
    if not data_file.is_file():
        return Skipped(folder.name, f"file #{idx} not in cache")

    # Only the pieces overlapping this file matter; Stremio skips the torrent's other files.
    offset = sum(l for _, l in files[:idx])
    first = offset // piece_len
    last = (offset + length - 1) // piece_len

    return CacheEntry(
        infohash=folder.name,
        index=idx,
        src=data_file,
        name=path.split("/")[-1],
        size=length,
        have=count_have(bitfield_path.read_bytes(), first, last),
        total=last - first + 1,
    )


def scan_cache(cache_dir: Path) -> list[CacheEntry | Skipped]:
    return [scan_entry(p) for p in sorted(cache_dir.iterdir()) if p.is_dir()]
