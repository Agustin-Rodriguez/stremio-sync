import json
from pathlib import Path

from stremio_sync.cache.scan import EPISODE, MOVIE, CacheEntry
from stremio_sync.state import SentLog


def entry(index=0, name="Movie.mkv", kind=MOVIE):
    return CacheEntry("a" * 40, index, Path(str(index)), name, 1234, 1, 1, kind)


def test_missing_file_is_empty(tmp_path):
    log = SentLog(tmp_path / "sent.json")
    assert log.records() == [] and entry() not in log


def test_mark_saves_and_reloads(tmp_path):
    path = tmp_path / "sent.json"
    SentLog(path).mark(entry(kind=EPISODE, name="S01E01.mkv"))
    log = SentLog(path)
    assert entry() in log
    [rec] = log.records()
    assert rec["name"] == "S01E01.mkv" and rec["size"] == 1234 and rec["kind"] == EPISODE
    assert "sent_at" in rec and not log.is_imported(entry())
    assert not (tmp_path / "sent.tmp").exists()


def test_mark_imported_matches_by_name_and_keeps_first_time(tmp_path):
    log = SentLog(tmp_path / "sent.json")
    log.mark(entry(0, "A.mkv"))
    log.mark(entry(1, "B.mkv"))
    log.mark_imported({"A.mkv"})
    first = log.records()[0]["imported_at"]
    log.mark_imported({"A.mkv", "unknown.mkv"})
    assert log.is_imported(entry(0)) and not log.is_imported(entry(1))
    assert log.records()[0]["imported_at"] == first


def test_reads_old_records_without_kind_or_size(tmp_path):
    path = tmp_path / "sent.json"
    path.write_text(json.dumps({f"{'a' * 40}:0": {"name": "Old.mkv", "sent_at": "2024-01-01T00:00:00"}}))
    log = SentLog(path)
    assert entry() in log
    log.mark_imported({"Old.mkv"})
    assert SentLog(path).is_imported(entry())
