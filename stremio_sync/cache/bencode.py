"""Minimal bencode decoder (the format torrent metadata is stored in)."""


def bdecode(data: bytes):
    """Decode a bencoded value. Raises ValueError on malformed input."""
    def parse(i):
        c = data[i:i + 1]
        if c == b"d":
            i += 1
            d = {}
            while data[i:i + 1] != b"e":
                k, i = parse(i)
                v, i = parse(i)
                d[k] = v
            return d, i + 1
        if c == b"l":
            i += 1
            items = []
            while data[i:i + 1] != b"e":
                v, i = parse(i)
                items.append(v)
            return items, i + 1
        if c == b"i":
            end = data.index(b"e", i)
            return int(data[i + 1:end]), end + 1
        if c.isdigit():
            colon = data.index(b":", i)
            start = colon + 1
            n = int(data[i:colon])
            return data[start:start + n], start + n
        raise ValueError(f"bad bencode at byte {i}")

    value, _ = parse(0)
    return value
