"""Reading pasted links, and picking which of a torrent's files to download."""

import base64
import re
import urllib.parse
from dataclasses import dataclass

from ..cache.scan import EPISODE, pick_files
from .errors import FetchError, Unsendable

HEX_HASH = re.compile(r"[0-9a-fA-F]{40}")
STREAM_LINK = re.compile(r"^https?://[^/]+/([0-9a-fA-F]{40})/(\d+)(?:[/?#]|$)")


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
