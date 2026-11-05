import pytest

from stremio_sync.clients.arr.base import ArrConfig, NoMatch
from stremio_sync.clients.arr.sonarr import Sonarr, SonarrError, _norm
from tests.fakes import FakeArrAPI

CFG = ArrConfig("http://sonarr", "key", "/data/tv", None, True)
FILE = "Doctor.Who.2005.S01E02.1080p.mkv"


def parse_reply(title="Doctor Who 2005", without_year="Doctor Who", year=2005, season=1, eps=(2,)):
    return {"parsedEpisodeInfo": {
        "seriesTitle": title,
        "seriesTitleInfo": {"title": title, "titleWithoutYear": without_year, "year": year},
        "seasonNumber": season, "episodeNumbers": list(eps)}}


def episode_list(series_id=7, season=1, n=3, has_file=False):
    return [{"id": 100 + e, "seriesId": series_id, "seasonNumber": season, "episodeNumber": e, "hasFile": has_file}
            for e in range(1, n + 1)]


@pytest.mark.parametrize("a, b", [
    ("Doctor Who (2005)", "Doctor Who"),
    ("Marvel's Agents of S.H.I.E.L.D.", "marvels agents of shield"),
    ("The Office (US)", "The Office US"),
])
def test_norm_equal(a, b):
    assert _norm(a) == _norm(b)


def test_norm_only_strips_a_trailing_year():
    assert _norm("1883") == "1883"
    assert _norm("Show (2005) Extra") != _norm("Show Extra")


def test_known_series_comes_straight_from_parse(monkeypatch):
    series = {"id": 7, "title": "Doctor Who"}
    eps = [{"id": 1}]
    FakeArrAPI({("GET", "parse"): {"series": series, "episodes": eps}}).install(monkeypatch)
    assert Sonarr(CFG).find_or_add_series(FILE) == (series, eps, False)


def test_year_in_file_name_matches_the_right_show(monkeypatch):
    """Regression for 632b72c: look up "Doctor Who", not "Doctor Who 2005", and pick by year."""
    existing = {"id": 7, "title": "Doctor Who (2005)"}
    api = FakeArrAPI({
        ("GET", "parse"): parse_reply(),
        ("GET", "series/lookup"): [{"title": "Doctor Who", "year": 1963, "tvdbId": 1},
                                   {"title": "Doctor Who (2005)", "year": 2005, "tvdbId": 2}],
        ("GET", "series"): lambda params, _: [existing] if params["tvdbId"] == 2 else [],
        ("GET", "episode"): episode_list(),
    }).install(monkeypatch)
    series, episodes, added = Sonarr(CFG).find_or_add_series(FILE)
    assert series is existing and not added
    assert [e["episodeNumber"] for e in episodes] == [2]
    assert api.called("GET", "series/lookup")[0][2] == {"term": "Doctor Who"}


def test_ambiguous_title_without_year_is_skipped(monkeypatch):
    FakeArrAPI({
        ("GET", "parse"): parse_reply(title="Doctor Who", year=0),
        ("GET", "series/lookup"): [{"title": "Doctor Who", "year": 1963, "tvdbId": 1},
                                   {"title": "Doctor Who (2005)", "year": 2005, "tvdbId": 2}],
    }).install(monkeypatch)
    with pytest.raises(NoMatch, match="2 shows match"):
        Sonarr(CFG).find_or_add_series(FILE)


def test_no_title_in_file_name(monkeypatch):
    FakeArrAPI({("GET", "parse"): {}}).install(monkeypatch)
    with pytest.raises(NoMatch, match="series title"):
        Sonarr(CFG).find_or_add_series("x.mkv")


def test_adds_new_series_unmonitored_search_and_waits_for_episodes(monkeypatch):
    lists = iter([[], episode_list(series_id=9)])  # the episode list fills in after a moment
    api = FakeArrAPI({
        ("GET", "parse"): parse_reply(),
        ("GET", "series/lookup"): [{"id": 0, "title": "Doctor Who (2005)", "year": 2005, "tvdbId": 2}],
        ("GET", "series"): [],
        ("GET", "rootfolder"): [{"path": "/tv"}],
        ("GET", "qualityprofile"): [{"id": 4, "name": "Any"}],
        ("GET", "languageprofile"): SonarrError("HTTP 404"),  # Sonarr v4
        ("POST", "series"): lambda _, body: {**body, "id": 9},
        ("GET", "episode"): lambda *_: next(lists),
    }).install(monkeypatch)
    series, episodes, added = Sonarr(CFG).find_or_add_series(FILE)
    assert added and series["id"] == 9 and [e["id"] for e in episodes] == [102]
    [(_, _, _, body)] = api.called("POST", "series")
    assert body["rootFolderPath"] == "/tv" and body["qualityProfileId"] == 4
    assert body["addOptions"] == {"monitor": "none", "searchForMissingEpisodes": False}
    assert "id" not in body and "languageProfileId" not in body


def test_missing_episode_in_known_series(monkeypatch):
    FakeArrAPI({
        ("GET", "parse"): parse_reply(eps=(9,)),
        ("GET", "series/lookup"): [{"title": "Doctor Who (2005)", "year": 2005, "tvdbId": 2}],
        ("GET", "series"): [{"id": 7, "title": "Doctor Who"}],
        ("GET", "episode"): episode_list(),
    }).install(monkeypatch)
    with pytest.raises(NoMatch, match="S01E09"):
        Sonarr(CFG).find_or_add_series(FILE)


def test_import_incoming_imports_monitors_and_skips(monkeypatch):
    series = {"id": 7, "title": "Show"}
    ok = {"path": "/data/tv/Show.S01E01.mkv", "series": series, "quality": {"q": 1},
          "episodes": [{"id": 101, "seasonNumber": 1, "episodeNumber": 1}]}
    has_file = {"path": "/data/tv/Show.S01E02.mkv", "series": series, "quality": {"q": 1},
                "episodes": [{"id": 102, "seasonNumber": 1, "episodeNumber": 2, "hasFile": True}]}
    no_quality = {"path": "/data/tv/Show.S01E03.mkv", "series": series, "quality": None,
                  "episodes": [{"id": 103, "seasonNumber": 1, "episodeNumber": 3}]}
    listings = iter([[ok, has_file, no_quality], [has_file, no_quality]])  # before and after the import
    api = FakeArrAPI({
        ("GET", "manualimport"): lambda *_: next(listings),
        ("PUT", "episode/monitor"): None,
        ("POST", "command"): {"id": 55},
        ("GET", "command/55"): {"status": "completed"},
    }).install(monkeypatch)
    problems, imported = Sonarr(CFG).import_incoming()
    assert (problems, imported) == (2, {"Show.S01E01.mkv"})
    assert api.called("PUT", "episode/monitor")[0][3] == {"episodeIds": [101], "monitored": True}
    [(_, _, _, cmd)] = api.called("POST", "command")
    assert cmd["files"][0]["episodeIds"] == [101] and cmd["importMode"] == "move"


def test_import_counts_a_file_still_in_incoming_as_failed(monkeypatch):
    item = {"path": "/data/tv/Show.S01E01.mkv", "series": {"id": 7, "title": "Show"}, "quality": {"q": 1},
            "episodes": [{"id": 101, "seasonNumber": 1, "episodeNumber": 1}]}
    FakeArrAPI({
        ("GET", "manualimport"): [item],  # still there afterwards
        ("PUT", "episode/monitor"): None,
        ("POST", "command"): {"id": 1},
        ("GET", "command/1"): {"status": "failed"},
    }).install(monkeypatch)
    assert Sonarr(CFG).import_incoming() == (1, set())


def test_nothing_waiting(monkeypatch):
    FakeArrAPI({("GET", "manualimport"): []}).install(monkeypatch)
    assert Sonarr(CFG).import_incoming() == (0, set())
