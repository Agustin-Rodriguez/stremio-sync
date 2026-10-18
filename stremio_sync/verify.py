"""Checking a movie file against the torrent's SHA-1 piece hashes."""

import hashlib

from .cache import CacheEntry, torrent_files


def verify(entry: CacheEntry, info: dict) -> bool:
    """True if every piece lying wholly inside the file matches its hash. Raises ValueError without hashes."""
    hashes = info.get(b"pieces")
    if not hashes:
        raise ValueError("torrent metadata has no piece hashes")
    piece_len = info[b"piece length"]
    files = torrent_files(info)
    offset = sum(l for _, l in files[:entry.index])
    end = offset + files[entry.index][1]
    total = sum(l for _, l in files)

    if entry.src.stat().st_size < end - offset:
        return False
    # Edge pieces that spill into neighbouring files can't be checked: Stremio doesn't download those files.
    first = -(-offset // piece_len)
    with entry.src.open("rb") as f:
        for p in range(first, len(hashes) // 20):
            start, stop = p * piece_len, min((p + 1) * piece_len, total)
            if stop > end:
                break
            f.seek(start - offset)
            if hashlib.sha1(f.read(stop - start)).digest() != hashes[p * 20:p * 20 + 20]:
                return False
    return True
