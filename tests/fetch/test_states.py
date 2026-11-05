import pytest

from stremio_sync.fetch.states import DONE, DOWNLOADING, FAILED, INFO, QUEUED, shown


def rec(state=DOWNLOADING, **kw):
    return {"state": state, "error": None, **kw}


@pytest.mark.parametrize("r, expected", [
    (rec(QUEUED), {"state": "Queued", "progress": ""}),
    (rec(DONE), {"state": "Done", "progress": "100%"}),
    (rec(FAILED, error="no peers"), {"state": "Failed: no peers", "progress": ""}),
    (rec(INFO), {"state": "Getting torrent info", "progress": ""}),
    (rec(), {"state": "Downloading", "progress": ""}),
])
def test_basic_states(r, expected):
    assert shown(r, held=False) == expected


def test_progress_rounds_down_and_shows_speed():
    r = rec(size=1000, done_bytes=997, speed=2_500_000)
    assert shown(r, False)["progress"] == "99%  2.5 MB/s"


def test_progress_without_size_is_blank():
    assert shown(rec(done_bytes=5), False)["progress"] == ""


def test_held_wins_over_everything_but_finished():
    assert shown(rec(down=True, stalled=True), held=True)["state"] == "Paused"
    assert shown(rec(DONE), held=True)["state"] == "Done"


def test_waiting_for_stremio_hides_retry_note():
    assert shown(rec(down=True, note="timeout"), False)["state"] == "Waiting for Stremio to open"


def test_stalled_and_retry_note():
    assert shown(rec(stalled=True, note="HTTP 500"), False)["state"] == "Stalled, no peers sending (retrying: HTTP 500)"


def test_episode_counter_in_pack():
    assert shown(rec(part=(2, 5)), False)["state"] == "Downloading episode 2 of 5"
    assert shown(rec(part=(1, 1)), False)["state"] == "Downloading"
