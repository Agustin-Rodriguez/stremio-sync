from pathlib import Path

import pytest

from stremio_sync.cache.scan import EPISODE, MOVIE, CacheEntry
from stremio_sync.tray import progress as progress_mod
from tests.fakes import FakeClock
from stremio_sync.tray.progress import (CHECKING, DAMAGED, DOWNLOADING, IMPORTING, STALLED_AFTER,
                                        UPLOADING, WAITING, Progress)

H = "a" * 40


def entry(index, name, kind=MOVIE, infohash=H):
    return CacheEntry(infohash, index, Path(str(index)), name, 10, 1, 1, kind)


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock(mono=500.0, wall=10_000.0)
    monkeypatch.setattr(progress_mod, "time", c)
    return c


def test_rows_sorted_by_state_then_name(clock):
    p = Progress()
    p.track(entry(0, "b.mkv"), IMPORTING)
    p.track(entry(1, "Z.mkv"), UPLOADING, started=400.0)
    p.track(entry(2, "a.mkv"), IMPORTING)
    assert [r["name"] for r in p.snapshot("queued")] == ["Z.mkv", "a.mkv", "b.mkv"]


def test_labels(clock):
    p = Progress()
    p.track(entry(0, "up.mkv"), UPLOADING, started=500.0 - 75)
    p.track(entry(1, "chk.mkv"), CHECKING)
    p.track(entry(2, "wait.mkv"), WAITING)
    p.track(entry(3, "bad.mkv"), DAMAGED)
    p.track(entry(4, "dl.mkv"), DOWNLOADING, have=2, total=3, mtime=clock.wall)
    p.track(entry(5, "ep.mkv", EPISODE), IMPORTING)
    p.track(entry(6, "mv.mkv"), IMPORTING)
    rows = {r["name"]: (r["state"], r["progress"]) for r in p.snapshot("paused")}
    assert rows == {
        "up.mkv": ("Sending", "1m15s"),
        "chk.mkv": ("Checking", ""),
        "wait.mkv": ("Waiting to send (paused)", ""),
        "bad.mkv": ("Damaged, waiting for Stremio", ""),
        "dl.mkv": ("Downloading", "66%"),
        "ep.mkv": ("Waiting for Sonarr", ""),
        "mv.mkv": ("Waiting for Radarr", ""),
    }


def test_download_goes_stalled_when_file_stops_changing(clock):
    p = Progress()
    p.track(entry(0, "dl.mkv"), DOWNLOADING, have=1, total=2, mtime=clock.wall)
    clock.advance(STALLED_AFTER + 1)
    assert p.snapshot("")[0]["state"] == "Stalled"


def test_untrack_and_untrack_folder(clock):
    p = Progress()
    p.track(entry(0, "a.mkv"), WAITING)
    p.track(entry(1, "b.mkv"), WAITING)
    p.track(entry(0, "other.mkv", infohash="b" * 40), WAITING)
    p.untrack_folder(H, keep={f"{H}:1"})
    assert {r["name"] for r in p.snapshot("")} == {"b.mkv", "other.mkv"}
    p.untrack(entry(1, "b.mkv"))
    assert [r["key"] for r in p.snapshot("")] == ["b" * 40 + ":0"]
