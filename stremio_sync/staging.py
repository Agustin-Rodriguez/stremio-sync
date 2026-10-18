"""Staging: hard links to movie files, so Stremio clearing its cache doesn't lose them."""

import dataclasses
import logging
import os
import shutil
from pathlib import Path

from .cache import CacheEntry, Skipped, scan_entry

log = logging.getLogger(__name__)


class StagingError(Exception):
    pass


def default_staging_dir(cache_dir: Path) -> Path:
    # Next to the cache, so it's on the same drive: hard links can't cross drives.
    return cache_dir.parent / "stremio-sync-staging"


def _copy_atomic(src: Path, dst: Path):
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)


class Staging:
    """Each torrent gets a folder laid out like Stremio's: `cache`, `bitfield` (a copy), `<index>` (a hard link)."""

    def __init__(self, root: Path):
        self.root = root

    def entry(self, infohash: str) -> CacheEntry | None:
        folder = self.root / infohash
        if not folder.is_dir():
            return None
        item = scan_entry(folder)
        return item if isinstance(item, CacheEntry) else None

    def entries(self) -> list[CacheEntry]:
        if not self.root.is_dir():
            return []
        return [e for e in (scan_entry(p) for p in sorted(self.root.iterdir()) if p.is_dir())
                if not isinstance(e, Skipped)]

    def protect(self, entry: CacheEntry) -> CacheEntry:
        """Link the entry's data file into staging and refresh the metadata copies. Raises StagingError."""
        folder = self.root / entry.infohash
        link = folder / entry.src.name
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if not link.exists():
                os.link(entry.src, link)
                log.debug("  staged %s", entry.name)
            elif not os.path.samefile(entry.src, link):
                # Stremio re-created its file. Keep whichever copy has more of the movie.
                ours = self.entry(entry.infohash)
                if ours is not None and ours.have > entry.have:
                    return ours
                tmp = link.with_name(link.name + ".tmp")
                tmp.unlink(missing_ok=True)
                os.link(entry.src, tmp)
                os.replace(tmp, link)
            _copy_atomic(entry.src.parent / "cache", folder / "cache")
            _copy_atomic(entry.src.parent / "bitfield", folder / "bitfield")
        except OSError as e:
            raise StagingError(f"can't stage {entry.name} in {self.root} "
                               f"(it must be on the same drive as the cache): {e}") from e
        return dataclasses.replace(entry, src=link)

    def drop(self, infohash: str):
        shutil.rmtree(self.root / infohash, ignore_errors=True)

    def count(self) -> int:
        return sum(1 for p in self.root.iterdir() if p.is_dir()) if self.root.is_dir() else 0
