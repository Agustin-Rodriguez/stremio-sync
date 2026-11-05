import pytest

from stremio_sync import pipeline
from stremio_sync.cache.scan import EPISODE, MOVIE, scan_folder
from stremio_sync.clients.arr.radarr import RadarrError
from stremio_sync.state import SentLog
from tests.fakes import FakeImporter, FakeSend, transfer_error
from tests.helpers import HASH_A, HASH_B, HASH_C, make_torrent


@pytest.fixture
def importers(monkeypatch):
    radarr, sonarr = FakeImporter(), FakeImporter()
    monkeypatch.setattr(pipeline, "IMPORTERS", (
        (MOVIE, "Radarr", radarr, RadarrError),
        (EPISODE, "Sonarr", sonarr, type("SonarrError", (Exception,), {})),
    ))
    return radarr, sonarr


@pytest.mark.parametrize("secs, text", [(0, "0m00s"), (65, "1m05s"), (3661, "1h01m01s")])
def test_fmt_duration(secs, text):
    assert pipeline.fmt_duration(secs) == text


def test_find_ready_returns_complete_unsent(cfg, cache_dir):
    make_torrent(cache_dir / HASH_A, [("Done.mkv", 32)])
    make_torrent(cache_dir / HASH_B, [("Partial.mkv", 32)], have={0})
    make_torrent(cache_dir / HASH_C, [("Sent.mkv", 32)])
    sent = SentLog(cfg.state_file)
    [already] = scan_folder(cache_dir / HASH_C)
    sent.mark(already)
    assert [e.name for e in pipeline.find_ready(cfg, sent)] == ["Done.mkv"]


def test_find_ready_skips_episodes_when_series_off(make_config, cache_dir):
    make_torrent(cache_dir / HASH_A, [("Show.S01E01.mkv", 32)])
    cfg = make_config(server_series_incoming=None)
    assert pipeline.find_ready(cfg, SentLog(cfg.state_file)) == []


def test_send_all_marks_each_and_counts_failures(cfg, cache_dir, monkeypatch):
    for h in (HASH_A, HASH_B, HASH_C):
        make_torrent(cache_dir / h, [(f"{h[0]}.mkv", 16)])
    send = FakeSend("sent", transfer_error(), "exists").install(monkeypatch)
    sent = SentLog(cfg.state_file)
    entries = pipeline.find_ready(cfg, sent)
    assert pipeline.send_all(entries, cfg, sent) == 1
    assert [c[2] for c in send.calls] == ["/srv/incoming"] * 3
    assert {r["name"] for r in SentLog(cfg.state_file).records()} == {"a.mkv", "c.mkv"}


def test_send_episode_goes_to_series_incoming(cfg, cache_dir, monkeypatch):
    make_torrent(cache_dir / HASH_A, [("Show.S01E01.mkv", 16)])
    send = FakeSend().install(monkeypatch)
    sent = SentLog(cfg.state_file)
    pipeline.send_all(pipeline.find_ready(cfg, sent), cfg, sent)
    assert send.calls == [(f"{HASH_A}:0", "me@server", "/srv/incoming-tv")]


def test_send_all_without_server(make_config, cache_dir, monkeypatch):
    make_torrent(cache_dir / HASH_A, [("a.mkv", 16)])
    cfg = make_config(ssh_target=None)
    send = FakeSend().install(monkeypatch)
    sent = SentLog(cfg.state_file)
    assert pipeline.send_all(pipeline.find_ready(cfg, sent), cfg, sent) == 1
    assert send.calls == []


def test_import_all_marks_imported_and_sums_problems(cfg, cache_dir, importers):
    radarr, sonarr = importers
    radarr.result = (1, {"a.mkv"})
    sonarr.result = (2, set())
    make_torrent(cache_dir / HASH_A, [("a.mkv", 16)])
    sent = SentLog(cfg.state_file)
    [entry] = scan_folder(cache_dir / HASH_A)
    sent.mark(entry)
    assert pipeline.import_all(cfg, sent) == 3
    assert sent.is_imported(entry)


def test_import_all_only_requested_kinds_and_configured(make_config, importers):
    radarr, sonarr = importers
    cfg = make_config(sonarr=None)
    pipeline.import_all(cfg, SentLog(cfg.state_file), {EPISODE})
    assert (radarr.runs, sonarr.runs) == (0, 0)
    pipeline.import_all(cfg, SentLog(cfg.state_file))
    assert (radarr.runs, sonarr.runs) == (1, 0)


def test_import_error_counts_as_one_problem(cfg, importers):
    importers[0].error = RadarrError("can't reach Radarr")
    assert pipeline.import_all(cfg, SentLog(cfg.state_file)) == 1


def test_run_dry_run_sends_nothing(cfg, cache_dir, monkeypatch, importers):
    make_torrent(cache_dir / HASH_A, [("a.mkv", 16)])
    send = FakeSend().install(monkeypatch)
    assert pipeline.run(cfg, send_files=False) == 0
    assert send.calls == [] and importers[0].runs == 0


def test_run_sends_then_imports(cfg, cache_dir, monkeypatch, importers):
    make_torrent(cache_dir / HASH_A, [("a.mkv", 16)])
    importers[0].result = (0, {"a.mkv"})
    FakeSend().install(monkeypatch)
    assert pipeline.run(cfg, send_files=True) == 0
    [rec] = SentLog(cfg.state_file).records()
    assert rec["imported_at"]
