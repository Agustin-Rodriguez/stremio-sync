import pytest

from stremio_sync.cache.bencode import bdecode
from tests.helpers import bencode


@pytest.mark.parametrize("data, expected", [
    (b"i42e", 42),
    (b"i-7e", -7),
    (b"i0e", 0),
    (b"4:spam", b"spam"),
    (b"0:", b""),
    (b"le", []),
    (b"l4:spami1ee", [b"spam", 1]),
    (b"de", {}),
    (b"d3:cow3:moo4:spaml1:a1:bee", {b"cow": b"moo", b"spam": [b"a", b"b"]}),
])
def test_decodes(data, expected):
    assert bdecode(data) == expected


def test_round_trips_a_torrent_like_structure():
    value = {b"info": {b"name": b"x.mkv", b"length": 123, b"piece length": 16, b"pieces": bytes(range(20))}}
    assert bdecode(bencode(value)) == value


def test_binary_strings_are_kept_as_bytes():
    raw = bytes(range(256))
    assert bdecode(bencode(raw)) == raw


@pytest.mark.parametrize("data", [b"x", b"i12", b"l", b"d1:ae", b"5:ab", b"d4:infod6:lengthi9e4:name10:short"])
def test_malformed_or_truncated_input_raises_value_error(data):
    with pytest.raises(ValueError):
        bdecode(data)
