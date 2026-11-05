from stremio_sync.cache.scan import (EPISODE, MOVIE, CacheEntry, Skipped, count_have, pick_files,
                                     scan_cache, scan_folder, torrent_files, video_files)
from tests.helpers import HASH_A, HASH_B, make_torrent


# --- torrent_files / video_files ---

def test_torrent_files_single_file():
    assert torrent_files({b"name": b"Movie.mkv", b"length": 10}) == [("Movie.mkv", 10)]


def test_torrent_files_multi_file_joins_path():
    info = {b"name": b"Pack", b"files": [{b"path": [b"Season 1", b"S01E01.mkv"], b"length": 5}]}
    assert torrent_files(info) == [("Season 1/S01E01.mkv", 5)]


def test_video_files_skips_samples_and_non_video():
    files = [("Movie.mkv", 100), ("Movie.nfo", 1), ("Sample/movie-sample.mkv", 5), ("Extra.MP4", 50)]
    assert video_files(files) == [(0, "Movie.mkv", 100), (3, "Extra.MP4", 50)]


# --- count_have ---

def test_count_have_reads_bits_msb_first():
    bits = bytes([0b10100000, 0b00000001])
    assert count_have(bits, 0, 7) == 2
    assert count_have(bits, 0, 15) == 3
    assert count_have(bits, 1, 1) == 0
    assert count_have(bits, 15, 15) == 1


def test_count_have_past_end_of_bitfield_counts_as_missing():
    assert count_have(b"\xff", 0, 15) == 8


# --- pick_files ---

def test_pick_single_movie():
    assert pick_files([("Movie.2020.mkv", 10), ("info.txt", 1)]) == (MOVIE, [(0, "Movie.2020.mkv", 10)])


def test_pick_season_pack_keeps_only_episodes():
    files = [("Show/Show.S01E01.mkv", 10), ("Show/Show.S01E02.mkv", 10), ("Show/Behind the scenes.mkv", 5)]
    assert pick_files(files) == (EPISODE, [(0, "Show/Show.S01E01.mkv", 10), (1, "Show/Show.S01E02.mkv", 10)])


def test_pick_1x02_episode_style():
    assert pick_files([("Show 1x02.mkv", 10)])[0] == EPISODE


def test_pick_episode_number_must_be_in_file_name_not_folder():
    # Folder says S01E01 but the file has no number: series-looking, no episode, skipped.
    assert isinstance(pick_files([("Show.S01E01/video.mkv", 10)]), str)


def test_pick_series_without_episode_numbers_is_skipped():
    assert "series" in pick_files([("Show Season 1/part.mkv", 10)])


def test_pick_several_movies_is_skipped():
    assert "several" in pick_files([("A.mkv", 10), ("B.mkv", 10)])


def test_pick_no_video_is_skipped():
    assert pick_files([("readme.txt", 1)]) == "no video file in torrent"


# --- scan_folder ---

def test_scan_complete_movie(cache_dir):
    make_torrent(cache_dir / HASH_A, [("Movie.2020.mkv", 40)])
    [entry] = scan_folder(cache_dir / HASH_A)
    assert entry == CacheEntry(HASH_A, 0, cache_dir / HASH_A / "0", "Movie.2020.mkv", 40, 3, 3, MOVIE)
    assert entry.complete
    assert entry.key == f"{HASH_A}:0"


def test_scan_partial_movie(cache_dir):
    make_torrent(cache_dir / HASH_A, [("Movie.mkv", 40)], have={0, 2})
    [entry] = scan_folder(cache_dir / HASH_A)
    assert (entry.have, entry.total, entry.complete) == (2, 3, False)


def test_scan_counts_only_pieces_covering_the_file(cache_dir):
    # 3 files of 20 bytes, 16-byte pieces: file 1 spans bytes 20..39 = pieces 1 and 2.
    files = [("Show.S01E01.mkv", 20), ("Show.S01E02.mkv", 20), ("Show.S01E03.mkv", 20)]
    make_torrent(cache_dir / HASH_A, files, present=[1], have={1, 2})
    [entry] = scan_folder(cache_dir / HASH_A)
    assert (entry.index, entry.kind, entry.have, entry.total) == (1, EPISODE, 2, 2)
    assert entry.name == "Show.S01E02.mkv"


def test_scan_season_pack_returns_only_streamed_episodes(cache_dir):
    files = [("Show.S01E01.mkv", 20), ("Show.S01E02.mkv", 20), ("Show.S01E03.mkv", 20)]
    make_torrent(cache_dir / HASH_A, files, present=[0, 2])
    assert [e.index for e in scan_folder(cache_dir / HASH_A)] == [0, 2]


def test_scan_reads_bare_info_dict_too(cache_dir):
    make_torrent(cache_dir / HASH_A, [("Movie.mkv", 16)], wrap_info=False)
    assert len(scan_folder(cache_dir / HASH_A)) == 1


def test_scan_skips_folder_without_metadata(cache_dir):
    (cache_dir / HASH_A).mkdir()
    assert scan_folder(cache_dir / HASH_A) == Skipped(HASH_A, "no cache metadata")


def test_scan_skips_folder_without_bitfield(cache_dir):
    make_torrent(cache_dir / HASH_A, [("Movie.mkv", 16)])
    (cache_dir / HASH_A / "bitfield").unlink()
    assert scan_folder(cache_dir / HASH_A) == Skipped(HASH_A, "no bitfield")


def test_scan_skips_garbage_metadata(cache_dir):
    make_torrent(cache_dir / HASH_A, [("Movie.mkv", 16)])
    (cache_dir / HASH_A / "cache").write_bytes(b"not bencode")
    assert scan_folder(cache_dir / HASH_A).reason.startswith("unreadable metadata")


def test_scan_skips_movie_not_downloaded_yet(cache_dir):
    make_torrent(cache_dir / HASH_A, [("Movie.mkv", 16)], present=[])
    assert scan_folder(cache_dir / HASH_A) == Skipped(HASH_A, "file #0 not in cache")


def test_scan_skips_pack_with_nothing_streamed(cache_dir):
    make_torrent(cache_dir / HASH_A, [("Show.S01E01.mkv", 16), ("Show.S01E02.mkv", 16)], present=[])
    assert scan_folder(cache_dir / HASH_A) == Skipped(HASH_A, "no streamed episode not in cache")


def test_scan_cache_mixes_entries_and_skips_in_folder_order(cache_dir):
    make_torrent(cache_dir / HASH_B, [("Movie.mkv", 16)])
    (cache_dir / HASH_A).mkdir()
    (cache_dir / "stray-file").write_text("x")
    items = scan_cache(cache_dir)
    assert [type(i) for i in items] == [Skipped, CacheEntry]
    assert items[1].infohash == HASH_B
