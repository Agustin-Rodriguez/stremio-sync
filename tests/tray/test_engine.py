"""The tray worker's pass over changed cache folders: stage, verify, send, import, unstage.

The watchdog observer and worker thread aren't started; tests call Engine._pass directly.
"""

import os

import pytest

from stremio_sync import pipeline
from stremio_sync.cache.scan import EPISODE, MOVIE
from stremio_sync.clients.arr.radarr import RadarrError
from stremio_sync.clients.arr.sonarr import SonarrError
from stremio_sync.state import SentLog
from stremio_sync.tray import engine as engine_mod
from stremio_sync.tray import progress as progress_mod
from stremio_sync.tray.engine import ERROR, PAUSE_SENDING, RUNNING, SEND_RETRY_AFTER, Engine
from tests.fakes import FakeClock, FakeImporter, FakeSend, transfer_error
from tests.helpers import HASH_A, HASH_B, make_torrent

MOVIE_FILES = [("Movie.2020.mkv", 48)]


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(engine_mod, "time", c)
    monkeypatch.setattr(progress_mod, "time", c)
    return c


@pytest.fixture(autouse=True)
def importers(monkeypatch):
    radarr, sonarr = FakeImporter(), FakeImporter()
    monkeypatch.setattr(pipeline, "IMPORTERS", ((MOVIE, "Radarr", radarr, RadarrError),
                                                (EPISODE, "Sonarr", sonarr, SonarrError)))
    return radarr, sonarr


@pytest.fixture
def make_engine(make_config, clock):
    statuses = []

    def make(**cfg_changes):
        e = Engine(make_config(**cfg_changes), on_status=lambda s, t: statuses.append((s, t)))
        e.statuses = statuses
        return e
    return make


def rows(engine):
    return {r["name"]: r["state"] for r in engine.progress()}


def test_complete_movie_goes_all_the_way(make_engine, cache_dir, monkeypatch, importers):
    make_torrent(cache_dir / HASH_A, MOVIE_FILES)
    send = FakeSend().install(monkeypatch)
    importers[0].result = (0, {"Movie.2020.mkv"})
    e = make_engine()
    assert e._pass({HASH_A}) == 0
    assert send.calls == [(f"{HASH_A}:0", "me@server", "/srv/incoming")]
    assert importers[0].runs == 1
    assert SentLog(e.cfg.state_file).records()[0]["imported_at"]
    assert e.staging.entries() == [] and rows(e) == {}


def test_partial_download_is_staged_and_shown(make_engine, cache_dir, monkeypatch):
    make_torrent(cache_dir / HASH_A, MOVIE_FILES, have={0})
    send = FakeSend().install(monkeypatch)
    e = make_engine()
    assert e._pass({HASH_A}) == 0
    assert os.path.samefile(e.staging.root / HASH_A / "0", cache_dir / HASH_A / "0")
    assert rows(e) == {"Movie.2020.mkv": "Downloading"}
    assert send.calls == []


def test_staged_copy_is_sent_after_stremio_clears_its_cache(make_engine, cache_dir, monkeypatch, importers):
    make_torrent(cache_dir / HASH_A, MOVIE_FILES, have={0})
    send = FakeSend().install(monkeypatch)
    e = make_engine()
    e._pass({HASH_A})
    make_torrent(cache_dir / HASH_A, MOVIE_FILES)       # finishes (same inode, so staging sees it)...
    for p in (cache_dir / HASH_A).iterdir():            # ...but Stremio clears it before we look
        p.unlink()
    (cache_dir / HASH_A).rmdir()
    e._full_scan = True
    e._pass(e._all_names())
    assert len(send.calls) == 1


def test_unfinished_file_dropped_by_stremio_is_unstaged(make_engine, cache_dir, monkeypatch):
    make_torrent(cache_dir / HASH_A, MOVIE_FILES, have={0})
    data = cache_dir / HASH_A / "0"
    data.write_bytes(data.read_bytes()[:16])  # only the first piece is really there
    FakeSend().install(monkeypatch)
    e = make_engine()
    e._pass({HASH_A})
    for p in (cache_dir / HASH_A).iterdir():
        p.unlink()
    e._pass({HASH_A})
    assert e.staging.entries() == [] and rows(e) == {}


def test_damaged_file_waits_and_isnt_checked_again_until_it_changes(make_engine, cache_dir, monkeypatch):
    make_torrent(cache_dir / HASH_A, MOVIE_FILES, corrupt=(0,))
    send = FakeSend().install(monkeypatch)
    e = make_engine()
    checks = []
    real_verify = engine_mod.verify
    monkeypatch.setattr(engine_mod, "verify", lambda *a: checks.append(1) or real_verify(*a))
    assert e._pass({HASH_A}) == 1
    assert rows(e) == {"Movie.2020.mkv": "Damaged, waiting for Stremio"}
    assert e._pass({HASH_A}) == 0 and len(checks) == 1
    make_torrent(cache_dir / HASH_A, MOVIE_FILES)        # Stremio rewrites the bad piece
    os.utime(cache_dir / HASH_A / "0", (1, 1))
    e._pass({HASH_A})
    assert len(checks) == 2 and len(send.calls) == 1


def test_failed_send_waits_before_retrying(make_engine, cache_dir, monkeypatch, clock, importers):
    make_torrent(cache_dir / HASH_A, MOVIE_FILES)
    send = FakeSend(transfer_error(), "sent").install(monkeypatch)
    e = make_engine()
    assert e._pass({HASH_A}) == 1
    assert rows(e) == {"Movie.2020.mkv": "Waiting to send (retrying in 5m00s)"}
    e._pass(set())
    assert len(send.calls) == 1
    clock.advance(SEND_RETRY_AFTER)
    e._pass(set())
    assert len(send.calls) == 2 and rows(e) == {"Movie.2020.mkv": "Waiting for Radarr"}


def test_pause_sending_verifies_but_holds_uploads(make_engine, cache_dir, monkeypatch):
    make_torrent(cache_dir / HASH_A, MOVIE_FILES)
    send = FakeSend().install(monkeypatch)
    e = make_engine()
    e.set_mode(PAUSE_SENDING)
    e._pass({HASH_A})
    assert send.calls == [] and rows(e) == {"Movie.2020.mkv": "Waiting to send (paused)"}
    e._report(0, False)
    assert e.status == (PAUSE_SENDING, "Sending paused, 1 file(s) waiting")
    e.set_mode(RUNNING)
    e._pass(set())
    assert len(send.calls) == 1


def test_no_importer_unstages_right_after_sending(make_engine, cache_dir, monkeypatch, importers):
    make_torrent(cache_dir / HASH_A, MOVIE_FILES)
    FakeSend().install(monkeypatch)
    e = make_engine(radarr=None)
    e._pass({HASH_A})
    assert e.staging.entries() == [] and importers[0].runs == 0


def test_not_imported_yet_stays_staged_and_retries_on_sync_now(make_engine, cache_dir, monkeypatch, importers):
    make_torrent(cache_dir / HASH_A, MOVIE_FILES)
    FakeSend().install(monkeypatch)
    e = make_engine()
    e._pass({HASH_A})
    assert len(e.staging.entries()) == 1 and rows(e) == {"Movie.2020.mkv": "Waiting for Radarr"}
    e._pass(set())
    assert importers[0].runs == 1  # nothing new sent: no import
    importers[0].result = (0, {"Movie.2020.mkv"})
    e.sync_now()
    e._pass(set())
    assert importers[0].runs == 2 and e.staging.entries() == []


def test_episodes_ignored_when_series_off(make_engine, cache_dir, monkeypatch):
    make_torrent(cache_dir / HASH_B, [("Show.S01E01.mkv", 16)])
    send = FakeSend().install(monkeypatch)
    e = make_engine(server_series_incoming=None)
    e._pass({HASH_B})
    assert send.calls == [] and e.staging.entries() == [] and rows(e) == {}


def test_episode_goes_to_sonarr(make_engine, cache_dir, monkeypatch, importers):
    make_torrent(cache_dir / HASH_B, [("Show.S01E01.mkv", 16), ("Show.S01E02.mkv", 16)], present=[1])
    send = FakeSend().install(monkeypatch)
    e = make_engine()
    e._pass({HASH_B})
    assert send.calls == [(f"{HASH_B}:1", "me@server", "/srv/incoming-tv")]
    assert (importers[0].runs, importers[1].runs) == (0, 1)


def test_changed_tracks_top_level_cache_folders_only(make_engine, cache_dir, tmp_path):
    e = make_engine()
    e.changed(str(cache_dir / HASH_A / "bitfield"))
    e.changed(str(cache_dir / HASH_B))
    e.changed(str(tmp_path / "elsewhere" / "x"))
    e.changed(str(e.staging.root / HASH_A / "0"))
    e.changed(str(cache_dir))
    assert e._dirty == {HASH_A, HASH_B}


def test_status_reports_errors_until_a_clean_full_scan(make_engine):
    e = make_engine()
    e._report(2, full=False)
    assert e.status == (ERROR, "Something went wrong, see the log")
    e._report(0, full=False)
    assert e.status[0] == ERROR
    e._report(0, full=True)
    assert e.status == (RUNNING, "Watching the cache")
    assert [s for s, _ in e.statuses] == [ERROR, RUNNING]
