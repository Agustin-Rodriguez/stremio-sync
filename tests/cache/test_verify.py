import pytest

from stremio_sync.cache.scan import scan_folder
from stremio_sync.cache.verify import verify
from tests.helpers import HASH_A, make_torrent


def entry_and_info(folder, files, index=0, **kw):
    info = make_torrent(folder, files, **kw)
    return next(e for e in scan_folder(folder) if e.index == index), info


def test_good_file_passes(cache_dir):
    entry, info = entry_and_info(cache_dir / HASH_A, [("Movie.mkv", 50)])
    assert verify(entry, info)


def test_flipped_byte_fails(cache_dir):
    entry, info = entry_and_info(cache_dir / HASH_A, [("Movie.mkv", 50)], corrupt=(0,))
    assert not verify(entry, info)


def test_short_file_fails(cache_dir):
    entry, info = entry_and_info(cache_dir / HASH_A, [("Movie.mkv", 50)])
    entry.src.write_bytes(entry.src.read_bytes()[:30])
    assert not verify(entry, info)


def test_middle_file_of_pack_checks_only_whole_pieces(cache_dir):
    # 16-byte pieces, files at 0..19, 20..59, 60..79. File 1's edge pieces (1 and 3) spill into
    # files Stremio never downloaded, so only piece 2 (32..47) can be and is checked.
    files = [("Show.S01E01.mkv", 20), ("Show.S01E02.mkv", 40), ("Show.S01E03.mkv", 20)]
    entry, info = entry_and_info(cache_dir / HASH_A, files, index=1, present=[1])
    assert verify(entry, info)


def test_middle_file_with_bad_inner_piece_fails(cache_dir):
    files = [("Show.S01E01.mkv", 20), ("Show.S01E02.mkv", 40), ("Show.S01E03.mkv", 20)]
    entry, info = entry_and_info(cache_dir / HASH_A, files, index=1, present=[1], corrupt=(1,))
    # The flipped byte is at offset 20 in the file = byte 40 in the torrent, inside piece 2.
    assert not verify(entry, info)


def test_no_piece_hashes_raises(cache_dir):
    entry, info = entry_and_info(cache_dir / HASH_A, [("Movie.mkv", 50)])
    with pytest.raises(ValueError):
        verify(entry, {**info, b"pieces": b""})
