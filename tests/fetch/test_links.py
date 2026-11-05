import base64

import pytest

from stremio_sync.cache.scan import EPISODE, MOVIE
from stremio_sync.fetch.errors import FetchError, Unsendable
from stremio_sync.fetch.links import Link, choose_files, parse_link
from tests.helpers import HASH_A

UPPER = "ABCDEF0123456789ABCDEF0123456789ABCDEF01"


# --- parse_link ---

def test_stream_link():
    assert parse_link(f"http://127.0.0.1:11470/{UPPER}/3/Movie.mkv") == Link(UPPER.lower(), 3)


@pytest.mark.parametrize("tail", ["", "/", "?tr=x", "#frag"])
def test_stream_link_endings(tail):
    assert parse_link(f"https://host/{HASH_A}/0{tail}").index == 0


def test_stream_link_needs_a_numeric_index():
    with pytest.raises(FetchError):
        parse_link(f"http://host/{HASH_A}/abc")


def test_magnet_with_hex_hash_trackers_and_name():
    link = parse_link(f"  magnet:?xt=urn:btih:{UPPER}&dn=My+Movie&tr=udp%3A%2F%2Ft1&tr=udp%3A%2F%2Ft2  ")
    assert link == Link(UPPER.lower(), None, ("udp://t1", "udp://t2"), "My Movie")


def test_magnet_with_base32_hash():
    raw = bytes(range(20))
    b32 = base64.b32encode(raw).decode()
    assert parse_link(f"magnet:?xt=urn:btih:{b32}").infohash == raw.hex()


def test_magnet_picks_the_btih_xt():
    link = parse_link(f"magnet:?xt=urn:sha1:zzz&xt=urn:btih:{HASH_A}")
    assert link.infohash == HASH_A


def test_magnet_with_unreadable_hash():
    with pytest.raises(FetchError, match="unreadable"):
        parse_link("magnet:?xt=urn:btih:nothex")


def test_bare_hash():
    assert parse_link(UPPER) == Link(UPPER.lower(), None)


@pytest.mark.parametrize("text", ["", "hello", "magnet:?dn=x", "http://example.com/movie.mkv", HASH_A[:-1]])
def test_garbage(text):
    with pytest.raises(FetchError, match="not a Stremio"):
        parse_link(text)


# --- choose_files ---

PACK = [("Show/Show.S01E01.mkv", 10), ("Show/Show.S01E02.mkv", 10), ("Show/info.nfo", 1)]


def test_choose_movie():
    assert choose_files(Link(HASH_A, None), [("Movie.mkv", 10)], series_on=False) == (MOVIE, [(0, "Movie.mkv", 10)])


def test_choose_whole_pack():
    kind, videos = choose_files(Link(HASH_A, None), PACK, series_on=True)
    assert kind == EPISODE and [v[0] for v in videos] == [0, 1]


def test_choose_one_episode_from_stream_link():
    assert choose_files(Link(HASH_A, 1), PACK, series_on=True) == (EPISODE, [(1, "Show/Show.S01E02.mkv", 10)])


def test_choose_episode_with_series_off():
    with pytest.raises(Unsendable, match="series syncing is off"):
        choose_files(Link(HASH_A, None), PACK, series_on=False)


def test_choose_index_that_isnt_a_video():
    with pytest.raises(Unsendable, match="isn't a movie or episode"):
        choose_files(Link(HASH_A, 2), PACK, series_on=True)


def test_choose_torrent_with_nothing_to_send():
    with pytest.raises(Unsendable, match="no video"):
        choose_files(Link(HASH_A, None), [("a.txt", 1)], series_on=True)
