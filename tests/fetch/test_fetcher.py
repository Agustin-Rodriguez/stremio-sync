"""The Add tab's downloader, driven step by step against a fake Stremio server.

Download threads aren't started: tests run Fetcher._run in the test thread with a ScriptedStop,
whose wait() (the pause between checks) is where each step changes the world.
"""

import json

import pytest

from stremio_sync.fetch import fetcher as fetcher_mod
from stremio_sync.fetch.errors import FetchError
from stremio_sync.fetch.fetcher import INFO_TIMEOUT, REOPEN_AFTER, STALLED_AFTER, Fetcher
from stremio_sync.fetch.states import DONE, DOWNLOADING, FAILED, QUEUED
from tests.fakes import FakeClock, FakeStremio, FakeThread, ScriptedStop, stremio_files
from tests.helpers import HASH_A, HASH_B, HASH_C, make_torrent

MOVIE_FILES = stremio_files(("Movie.2020.mkv", 1000), ("Movie.nfo", 10))
PACK_FILES = stremio_files(("Show/Show.S01E01.mkv", 500), ("Show/Show.S01E02.mkv", 500), ("Show/notes.txt", 1))


@pytest.fixture
def world(monkeypatch, cfg):
    """A Fetcher wired to fakes. world.run(fid, *steps) runs one download to the end."""
    class World:
        server = FakeStremio({HASH_A: {"name": "Movie", "files": MOVIE_FILES},
                              HASH_B: {"name": "Show S01", "files": PACK_FILES},
                              HASH_C: {"name": "Other", "files": MOVIE_FILES}})
        clock = FakeClock()
        threads = FakeThread.install(monkeypatch, fetcher_mod)
        fetched: list[str] = []
        path = cfg.state_file.with_name("fetches.json")

        def make(self, **cfg_changes):
            c = cfg.__class__(**{**cfg.__dict__, **cfg_changes})
            return Fetcher(c, self.path, on_fetched=self.fetched.append)

        def run(self, f, fid, *steps):
            stop = ScriptedStop(*steps)
            f._stops[fid] = stop
            f._run(fid, stop)
            return f.snapshot()

        def saved(self):
            return json.loads(self.path.read_text())

    w = World()
    monkeypatch.setattr(fetcher_mod, "StremioServer", lambda url: w.server)
    monkeypatch.setattr(fetcher_mod, "time", w.clock)
    monkeypatch.setattr(fetcher_mod, "CHECK_EVERY", 0)
    return w


def progress_to(world, infohash, idx, value):
    def step():
        world.server.progress_of[(infohash, idx)] = value
    return step


def downloads(world):
    """Download threads started (not the short-lived ones that open a file)."""
    return [t for t in world.threads.started if t.name != "fetch-open"]


def row(f, fid):
    return next(r for r in f.snapshot() if r["key"] == fid)


# --- the list: add, duplicates, cancel, clear ---

def test_add_queues_saves_and_schedules(world):
    f = world.make()
    f.start()
    f.add(f"magnet:?xt=urn:btih:{HASH_A}&dn=My+Movie")
    [r] = f.snapshot()
    assert (r["name"], r["phase"], r["state"]) == ("My Movie", QUEUED, "Queued")
    assert [t.name for t in downloads(world)] == [f"fetch-{r['key']}"]
    assert world.saved()[0]["infohash"] == HASH_A


def test_add_rejects_garbage_and_duplicates(world):
    f = world.make()
    with pytest.raises(FetchError, match="not a Stremio"):
        f.add("hello")
    f.add(HASH_B)
    with pytest.raises(FetchError, match="already in the list"):
        f.add(f"http://127.0.0.1:11470/{HASH_B}/1")  # whole pack is queued, so its episode is too
    assert len(f.snapshot()) == 1


def test_different_episodes_of_same_pack_are_not_duplicates(world):
    f = world.make()
    f.add(f"http://127.0.0.1:11470/{HASH_B}/0")
    f.add(f"http://127.0.0.1:11470/{HASH_B}/1")
    assert len(f.snapshot()) == 2


def test_only_max_downloads_run_at_once_then_the_next_starts(world):
    f = world.make(max_downloads=2)
    f.start()
    for h in (HASH_A, HASH_B, HASH_C):
        f.add(h)
    assert len(downloads(world)) == 2
    first = downloads(world)[0].args[0]
    world.run(f, first, progress_to(world, HASH_A, 0, 1.0))
    assert len(downloads(world)) == 3
    assert downloads(world)[2].args[0] == f.snapshot()[2]["key"]


def test_cancel_and_clear_finished(world):
    f = world.make()
    f.add(HASH_A)
    f.add(HASH_C)
    a, c = (r["key"] for r in f.snapshot())
    world.run(f, a, progress_to(world, HASH_A, 0, 1.0))
    f.cancel(c)
    assert [r["key"] for r in f.snapshot()] == [a]
    f.clear_finished()
    assert f.snapshot() == [] and world.saved() == []


# --- one download ---

def test_movie_downloads_to_done(world):
    f = world.make()
    f.add(f"magnet:?xt=urn:btih:{HASH_A}&tr=udp://t")
    fid = f.snapshot()[0]["key"]
    world.server.info_after[HASH_A] = 1  # first create() has no file list yet
    seen = []
    rows = world.run(f, fid,
                     lambda: seen.append(row(f, fid)["state"]),        # waiting for info
                     progress_to(world, HASH_A, 0, 0.4),
                     lambda: seen.append(row(f, fid)),
                     progress_to(world, HASH_A, 0, 1.0))
    assert seen[0] == "Getting torrent info"
    assert seen[1]["state"] == "Downloading" and seen[1]["progress"].startswith("40%")
    assert rows[0]["state"] == "Done" and rows[0]["name"] == "Movie.2020.mkv"
    assert world.fetched == [HASH_A]
    assert ("select", HASH_A, 0) in world.server.calls
    [saved] = world.saved()
    assert saved["state"] == DONE and saved["files"] == [[0, "Movie.2020.mkv", 1000]] and saved["kind"] == "movie"


def test_progress_column_after_a_check(world):
    f = world.make()
    f.add(HASH_A)
    fid = f.snapshot()[0]["key"]
    seen = []
    world.run(f, fid,
              lambda: None,                                     # got the file list
              lambda: None,                                     # opened it, 0%
              lambda: (world.clock.advance(0.0001),             # 500 of 1000 bytes in 0.1 ms
                       progress_to(world, HASH_A, 0, 0.5)()),
              lambda: seen.append(row(f, fid)["progress"]))
    assert seen == ["50%  5.0 MB/s"]


def test_season_pack_downloads_every_episode_in_order(world):
    f = world.make()
    f.add(HASH_B)
    fid = f.snapshot()[0]["key"]
    seen = []
    rows = world.run(f, fid,
                     lambda: None,
                     lambda: seen.append(row(f, fid)["state"]),
                     progress_to(world, HASH_B, 0, 1.0),
                     lambda: None,                                 # episode 1 done
                     lambda: seen.append(row(f, fid)["state"]),    # episode 2 opened
                     progress_to(world, HASH_B, 1, 1.0))
    assert seen == ["Downloading episode 1 of 2", "Downloading episode 2 of 2"]
    assert rows[0]["state"] == "Done" and rows[0]["name"] == "Show S01" and rows[0]["size"] == 1000
    assert world.fetched == [HASH_B, HASH_B]
    assert [c[2] for c in world.server.calls if c[0] == "select"] == [0, 1]


def test_pack_with_series_off_fails(world):
    f = world.make(server_series_incoming=None)
    f.add(HASH_B)
    rows = world.run(f, f.snapshot()[0]["key"])
    assert rows[0]["phase"] == FAILED and "series syncing is off" in rows[0]["state"]


def test_complete_in_cache_counts_even_if_stremio_says_99(world, cfg):
    f = world.make()
    f.add(HASH_A)
    fid = f.snapshot()[0]["key"]
    world.server.progress_of[(HASH_A, 0)] = 0.999
    rows = world.run(f, fid, lambda: None,
                     lambda: make_torrent(cfg.cache_dir / HASH_A, [("Movie.2020.mkv", 64), ("Movie.nfo", 10)],
                                          present=[0]))
    assert rows[0]["state"] == "Done"


def test_stremio_closing_waits_then_reopens_the_file(world):
    f = world.make()
    f.add(HASH_A)
    fid = f.snapshot()[0]["key"]
    seen = []

    def close():
        world.server.down = True
        world.server.running.clear()  # a restarted Stremio has forgotten the torrent

    def reopen():
        world.server.down = False

    rows = world.run(f, fid,
                     lambda: None, lambda: None,                 # info, then opened once
                     close,
                     lambda: seen.append(row(f, fid)["state"]),
                     lambda: world.clock.advance(INFO_TIMEOUT * 2),  # downtime doesn't time anything out
                     reopen,
                     progress_to(world, HASH_A, 0, 1.0))
    assert seen == ["Waiting for Stremio to open"]
    assert rows[0]["state"] == "Done"
    assert [c for c in world.server.calls if c[0] == "select"] == [("select", HASH_A, 0)] * 2


def test_stall_is_shown_then_the_file_is_opened_again(world):
    f = world.make()
    f.add(HASH_A)
    fid = f.snapshot()[0]["key"]
    world.server.progress_of[(HASH_A, 0)] = 0.3
    seen = []
    world.run(f, fid,
              lambda: None, lambda: None,
              lambda: world.clock.advance(STALLED_AFTER + 1),
              lambda: seen.append(row(f, fid)["state"]),
              lambda: world.clock.advance(REOPEN_AFTER))
    assert seen == ["Stalled, no peers sending"]
    assert len([c for c in world.server.calls if c[0] == "select"]) == 2


def test_no_info_after_timeout_fails(world):
    f = world.make()
    f.add(HASH_A)
    world.server.info_after[HASH_A] = 10**6
    rows = world.run(f, f.snapshot()[0]["key"], lambda: world.clock.advance(INFO_TIMEOUT + 1), lambda: None)
    assert rows[0]["phase"] == FAILED and "no torrent info after 10 minutes" in rows[0]["state"]


def test_held_doesnt_ask_stremio_and_shows_paused(world):
    f = world.make()
    f.add(HASH_A)
    fid = f.snapshot()[0]["key"]
    f.hold(True)
    world.clock.advance(INFO_TIMEOUT * 2)
    rows = world.run(f, fid, lambda: None)
    assert rows[0]["state"] == "Paused"
    assert world.server.calls == []


def test_stremio_error_shows_as_retry_note(world):
    from stremio_sync.clients.stremio_server import StremioError
    f = world.make()
    f.add(HASH_A)
    fid = f.snapshot()[0]["key"]
    seen = []

    def fail_once():
        world.server.on_progress = None
        raise StremioError("Stremio answered 500 for /stats.json")

    def arm():
        world.server.on_progress = lambda *_: fail_once()

    world.run(f, fid, lambda: None, arm, lambda: seen.append(row(f, fid)["state"]))
    assert seen == ["Downloading (retrying: Stremio answered 500 for /stats.json)"]


def test_crash_marks_failed_and_keeps_going(world, monkeypatch):
    f = world.make()
    f.add(HASH_A)
    monkeypatch.setattr(f, "_work", lambda *_: 1 / 0)
    rows = world.run(f, f.snapshot()[0]["key"])
    assert rows[0]["phase"] == FAILED and "unexpected error" in rows[0]["state"]


def test_cancel_mid_download_stops_without_saving_it_back(world):
    f = world.make()
    f.add(HASH_A)
    fid = f.snapshot()[0]["key"]
    world.run(f, fid, lambda: None, lambda: f.cancel(fid), lambda: None)
    assert f.snapshot() == [] and world.saved() == [] and world.fetched == []


# --- fetches.json across restarts ---

def test_restart_resumes_where_it_was(world):
    f = world.make()
    for h in (HASH_A, HASH_B, HASH_C):
        f.add(h)
    a, b, c = (r["key"] for r in f.snapshot())
    world.run(f, a, progress_to(world, HASH_A, 0, 1.0))                    # A: done
    world.run(f, b, lambda: None, progress_to(world, HASH_B, 0, 1.0),      # B: episode 1 of 2
              lambda: f.stop())                                            # then the app quits
    f2 = world.make()
    phases = {r["key"]: r["phase"] for r in f2.snapshot()}
    assert phases == {a: DONE, b: DOWNLOADING, c: QUEUED}
    world.server.calls.clear()
    world.run(f2, b, progress_to(world, HASH_B, 1, 1.0))
    assert row(f2, b)["state"] == "Done"
    assert ("select", HASH_B, 0) not in world.server.calls  # episode 1 isn't downloaded again


def test_unreadable_fetches_json_starts_empty(world):
    world.path.write_text("{not json")
    assert world.make().snapshot() == []


def test_stop_prevents_new_downloads(world):
    f = world.make()
    f.stop()
    f.add(HASH_A)
    assert world.threads.started == []


# --- cache folder check ---

def test_warns_once_if_stremio_caches_elsewhere(world, caplog, tmp_path):
    world.server.root = tmp_path / "elsewhere"
    f = world.make()
    f.add(HASH_A)
    world.run(f, f.snapshot()[0]["key"], lambda: None, lambda: None, progress_to(world, HASH_A, 0, 1.0))
    warnings = [r for r in caplog.records if "stremio-sync watches" in r.getMessage()]
    assert len(warnings) == 1


def test_no_warning_when_cache_matches(world, caplog, cfg):
    world.server.root = cfg.cache_dir.parent
    f = world.make()
    f.add(HASH_A)
    world.run(f, f.snapshot()[0]["key"], progress_to(world, HASH_A, 0, 1.0))
    assert not [r for r in caplog.records if "stremio-sync watches" in r.getMessage()]
