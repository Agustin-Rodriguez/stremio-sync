import os

import pytest

from stremio_sync.cache.scan import scan_folder
from stremio_sync.cache.staging import Staging, StagingError, default_staging_dir
from tests.helpers import HASH_A, HASH_B, make_torrent


@pytest.fixture
def staging(tmp_path):
    return Staging(tmp_path / "staging")


def scan_one(folder, index=0):
    return next(e for e in scan_folder(folder) if e.index == index)


def test_default_dir_is_next_to_the_cache(cache_dir):
    assert default_staging_dir(cache_dir) == cache_dir.parent / "stremio-sync-staging"


def test_protect_hard_links_data_and_copies_metadata(cache_dir, staging):
    make_torrent(cache_dir / HASH_A, [("Movie.mkv", 40)])
    entry = scan_one(cache_dir / HASH_A)
    staged = staging.protect(entry)
    assert staged.src == staging.root / HASH_A / "0"
    assert os.path.samefile(staged.src, entry.src)
    assert (staging.root / HASH_A / "cache").read_bytes() == (cache_dir / HASH_A / "cache").read_bytes()
    assert (staging.root / HASH_A / "bitfield").is_file()


def test_staged_copy_survives_stremio_clearing_its_cache(cache_dir, staging):
    make_torrent(cache_dir / HASH_A, [("Movie.mkv", 40)])
    staging.protect(scan_one(cache_dir / HASH_A))
    for p in (cache_dir / HASH_A).iterdir():
        p.unlink()
    [entry] = staging.entries()
    assert entry.complete and entry.src.stat().st_size == 40


def test_protect_again_refreshes_metadata(cache_dir, staging):
    make_torrent(cache_dir / HASH_A, [("Movie.mkv", 48)], have={0})
    staging.protect(scan_one(cache_dir / HASH_A))
    make_torrent(cache_dir / HASH_A, [("Movie.mkv", 48)])  # Stremio wrote the rest (same inode)
    staging.protect(scan_one(cache_dir / HASH_A))
    [entry] = staging.entries_in(HASH_A)
    assert entry.complete


def test_recreated_file_with_less_data_keeps_ours(cache_dir, staging):
    make_torrent(cache_dir / HASH_A, [("Movie.mkv", 48)])
    staging.protect(scan_one(cache_dir / HASH_A))
    (cache_dir / HASH_A / "0").unlink()
    make_torrent(cache_dir / HASH_A, [("Movie.mkv", 48)], have={0})  # Stremio started over
    result = staging.protect(scan_one(cache_dir / HASH_A))
    assert result.complete
    assert not os.path.samefile(result.src, cache_dir / HASH_A / "0")


def test_protect_error_is_staging_error(cache_dir, staging, monkeypatch):
    make_torrent(cache_dir / HASH_A, [("Movie.mkv", 16)])
    def no_links(*_):
        raise OSError("cross-device link")
    monkeypatch.setattr(os, "link", no_links)
    with pytest.raises(StagingError, match="same drive"):
        staging.protect(scan_one(cache_dir / HASH_A))


def test_drop_removes_file_then_folder_when_empty(cache_dir, staging):
    make_torrent(cache_dir / HASH_A, [("Show.S01E01.mkv", 16), ("Show.S01E02.mkv", 16)])
    e1, e2 = scan_folder(cache_dir / HASH_A)
    staging.protect(e1)
    staging.protect(e2)
    staging.drop(e1)
    assert [e.index for e in staging.entries_in(HASH_A)] == [1]
    staging.drop(e2)
    assert not (staging.root / HASH_A).exists()


def test_entries_and_count(cache_dir, staging):
    assert staging.entries() == [] and staging.count() == 0
    for h in (HASH_A, HASH_B):
        make_torrent(cache_dir / h, [("Movie.mkv", 16)])
        staging.protect(scan_one(cache_dir / h))
    assert [e.infohash for e in staging.entries()] == [HASH_A, HASH_B]
    assert staging.count() == 2
