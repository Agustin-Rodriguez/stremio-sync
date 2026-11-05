"""Building fake Stremio cache folders: a bencoded `cache` file, a `bitfield`, and one data file per index."""

import hashlib
from pathlib import Path

HASH_A = "a" * 40
HASH_B = "b" * 40
HASH_C = "c" * 40


def bencode(value) -> bytes:
    if isinstance(value, bool):
        raise TypeError("bencode has no booleans")
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, list):
        return b"l" + b"".join(bencode(v) for v in value) + b"e"
    if isinstance(value, dict):
        items = sorted((k.encode() if isinstance(k, str) else k, v) for k, v in value.items())
        return b"d" + b"".join(bencode(k) + bencode(v) for k, v in items) + b"e"
    raise TypeError(f"can't bencode {type(value).__name__}")


def content(index: int, length: int) -> bytes:
    """Deterministic, different bytes for each file of a torrent."""
    return bytes((index * 31 + j) % 251 for j in range(length))


def info_dict(files: list[tuple[str, int]], piece_len: int, name: str = "Torrent") -> dict:
    blob = b"".join(content(i, length) for i, (_, length) in enumerate(files))
    pieces = b"".join(hashlib.sha1(blob[p:p + piece_len]).digest() for p in range(0, len(blob), piece_len))
    info = {b"piece length": piece_len, b"pieces": pieces}
    if len(files) == 1 and "/" not in files[0][0]:
        info[b"name"] = files[0][0].encode()
        info[b"length"] = files[0][1]
    else:
        info[b"name"] = name.encode()
        info[b"files"] = [{b"path": [p.encode() for p in path.split("/")], b"length": length}
                          for path, length in files]
    return info


def bitfield(have: set[int], npieces: int) -> bytes:
    out = bytearray((npieces + 7) // 8)
    for p in have:
        out[p // 8] |= 0x80 >> (p % 8)
    return bytes(out)


def make_torrent(folder: Path, files: list[tuple[str, int]], piece_len: int = 16,
                 present: list[int] | None = None, have: set[int] | None = None,
                 corrupt: tuple[int, ...] = (), wrap_info: bool = True) -> dict:
    """Write a cache folder like Stremio's. Returns the info dict.

    present: which file indices have a data file (default: all). have: downloaded pieces (default: all).
    corrupt: indices whose data file gets one byte flipped.
    """
    info = info_dict(files, piece_len)
    total = sum(length for _, length in files)
    npieces = -(-total // piece_len)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "cache").write_bytes(bencode({b"info": info} if wrap_info else info))
    (folder / "bitfield").write_bytes(bitfield(set(range(npieces)) if have is None else have, npieces))
    for i in range(len(files)) if present is None else present:
        data = bytearray(content(i, files[i][1]))
        if i in corrupt:
            data[len(data) // 2] ^= 0xFF
        (folder / str(i)).write_bytes(bytes(data))
    return info
