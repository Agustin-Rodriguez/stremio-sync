"""Reading Stremio's cache: which movies and episodes are in it and how much of each is downloaded."""

import os
import platform
import re
from dataclasses import dataclass
from pathlib import Path

from .bencode import bdecode

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov"}
SERIES_PATTERN = re.compile(r"(s\d{1,2}e\d{1,2}|\d{1,2}x\d{2}|season\s?\d{1,2})", re.IGNORECASE)
EPISODE_PATTERN = re.compile(r"(s\d{1,2}e\d{1,3}|\b\d{1,2}x\d{2,3}\b)", re.IGNORECASE)
MOVIE, EPISODE = "movie", "episode"


@dataclass(frozen=True)
class CacheEntry:
    """A movie or episode file inside one cached torrent."""
    infohash: str
    index: int    # file number within the torrent, which is also its file name in the cache folder
    src: Path
    name: str     # real file name, from the torrent metadata
    size: int
    have: int     # pieces downloaded, counting only the pieces that cover this file
    total: int
    kind: str     # MOVIE or EPISODE

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


def video_files(files: list[tuple[str, int]]) -> list[tuple[int, str, int]]:
    """(index, path, length) of each video file that isn't a sample."""
    return [
        (idx, path, length)
        for idx, (path, length) in enumerate(files)
        if Path(path).suffix.lower() in VIDEO_EXTS and "sample" not in path.lower()
    ]


def count_have(bitfield: bytes, first: int, last: int) -> int:
    have = 0
    for p in range(first, last + 1):
        byte = p // 8
        if byte < len(bitfield) and (bitfield[byte] >> (7 - p % 8)) & 1:
            have += 1
    return have


def read_info(folder: Path) -> dict:
    """The torrent's info dict, from the folder's `cache` file. Raises ValueError if malformed."""
    meta = bdecode((folder / "cache").read_bytes())
    return meta.get(b"info", meta)


def pick_files(files: list[tuple[str, int]]) -> tuple[str, list[tuple[int, str, int]]] | str:
    """(kind, videos to sync) for a torrent, or why it's skipped."""
    videos = video_files(files)
    if not videos:
        return "no video file in torrent"
    episodes = [v for v in videos if EPISODE_PATTERN.search(v[1].split("/")[-1])]
    if episodes:
        return EPISODE, episodes  # anything else in a season pack (extras, specials) is left alone
    if any(SERIES_PATTERN.search(v[1]) for v in videos):
        return "looks like a series but has no episode numbers, skipping"
    if len(videos) > 1:
        return "several video files and no episode numbers, skipping"
    return MOVIE, videos


def scan_folder(folder: Path) -> list[CacheEntry] | Skipped:
    """The movie, or the streamed episodes, in one cache folder."""
    cache, bitfield_path = folder / "cache", folder / "bitfield"
    if not cache.is_file():
        return Skipped(folder.name, "no cache metadata")
    if not bitfield_path.is_file():
        return Skipped(folder.name, "no bitfield")

    try:
        info = read_info(folder)
        piece_len = info[b"piece length"]
        files = torrent_files(info)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as e:
        return Skipped(folder.name, f"unreadable metadata ({e!r})")

    picked = pick_files(files)
    if isinstance(picked, str):
        return Skipped(folder.name, picked)
    kind, videos = picked

    # Stremio only creates a data file for what was streamed, so unwatched episodes have none.
    present = [v for v in videos if (folder / str(v[0])).is_file()]
    if not present:
        what = f"file #{videos[0][0]}" if kind == MOVIE else "no streamed episode"
        return Skipped(folder.name, f"{what} not in cache")

    bitfield = bitfield_path.read_bytes()
    return [make_entry(folder, files, v, piece_len, bitfield, kind) for v in present]


def make_entry(folder: Path, files: list[tuple[str, int]], video: tuple[int, str, int],
               piece_len: int, bitfield: bytes, kind: str) -> CacheEntry:
    idx, path, length = video
    # Only the pieces overlapping this file matter; Stremio skips the torrent's other files.
    offset = sum(l for _, l in files[:idx])
    first = offset // piece_len
    last = (offset + length - 1) // piece_len
    return CacheEntry(
        infohash=folder.name,
        index=idx,
        src=folder / str(idx),
        name=path.split("/")[-1],
        size=length,
        have=count_have(bitfield, first, last),
        total=last - first + 1,
        kind=kind,
    )


def scan_cache(cache_dir: Path) -> list[CacheEntry | Skipped]:
    items = []
    for p in sorted(cache_dir.iterdir()):
        if p.is_dir():
            found = scan_folder(p)
            items += found if isinstance(found, list) else [found]
    return items
