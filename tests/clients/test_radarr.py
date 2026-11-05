import pytest

from stremio_sync.clients.arr.base import ArrConfig, NoMatch
from stremio_sync.clients.arr.radarr import Radarr, RadarrError
from tests.fakes import FakeArrAPI

CFG = ArrConfig("http://radarr", "key", "/data/in", "HD", False)


def parse_reply(title="Heat", year=1995):
    return {"parsedMovieInfo": {"movieTitles": [title], "year": year}}


def test_known_movie_from_parse(monkeypatch):
    movie = {"id": 1, "title": "Heat"}
    FakeArrAPI({("GET", "parse"): {"movie": movie}}).install(monkeypatch)
    assert Radarr(CFG).find_or_add_movie("Heat.1995.mkv") == (movie, False)


def test_needs_title_and_year(monkeypatch):
    FakeArrAPI({("GET", "parse"): parse_reply(year=0)}).install(monkeypatch)
    with pytest.raises(NoMatch, match="title and year"):
        Radarr(CFG).find_or_add_movie("Heat.mkv")


def test_exact_year_only(monkeypatch):
    FakeArrAPI({
        ("GET", "parse"): parse_reply(),
        ("GET", "movie/lookup"): [{"title": "Heat", "year": 1986, "tmdbId": 5}],
    }).install(monkeypatch)
    with pytest.raises(NoMatch, match="no exact match"):
        Radarr(CFG).find_or_add_movie("Heat.1995.mkv")


def test_adds_with_named_quality_profile_and_no_search(monkeypatch):
    api = FakeArrAPI({
        ("GET", "parse"): parse_reply(),
        ("GET", "movie/lookup"): [{"title": "Heat", "year": 1995, "tmdbId": 949}],
        ("GET", "movie"): [],
        ("GET", "rootfolder"): [{"path": "/movies"}],
        ("GET", "qualityprofile"): [{"id": 1, "name": "Any"}, {"id": 6, "name": "hd"}],
        ("POST", "movie"): lambda _, body: {**body, "id": 3},
    }).install(monkeypatch)
    movie, added = Radarr(CFG).find_or_add_movie("Heat.1995.mkv")
    assert added and movie["id"] == 3
    body = api.called("POST", "movie")[0][3]
    assert body["qualityProfileId"] == 6 and body["monitored"] is False
    assert body["addOptions"] == {"searchForMovie": False}


def test_unknown_quality_profile(monkeypatch):
    FakeArrAPI({
        ("GET", "parse"): parse_reply(),
        ("GET", "movie/lookup"): [{"title": "Heat", "year": 1995, "tmdbId": 949}],
        ("GET", "movie"): [],
        ("GET", "rootfolder"): [{"path": "/movies"}],
        ("GET", "qualityprofile"): [{"id": 1, "name": "Any"}],
    }).install(monkeypatch)
    with pytest.raises(RadarrError, match="quality profile named 'HD'"):
        Radarr(CFG).find_or_add_movie("Heat.1995.mkv")


def test_import_incoming(monkeypatch):
    good = {"path": "/data/in/Heat.1995.mkv", "movie": {"id": 1, "title": "Heat"}, "quality": {"q": 1}}
    dupe = {"path": "/data/in/Alien.mkv", "movie": {"id": 2, "title": "Alien", "hasFile": True}, "quality": {"q": 1}}
    unknown = {"path": "/data/in/Mystery.mkv", "quality": {"q": 1}}
    listings = iter([[good, dupe, unknown], [dupe, unknown]])
    api = FakeArrAPI({
        ("GET", "manualimport"): lambda *_: next(listings),
        ("GET", "parse"): {},  # Mystery.mkv: no title, no year
        ("POST", "command"): {"id": 8},
        ("GET", "command/8"): {"status": "completed"},
    }).install(monkeypatch)
    assert Radarr(CFG).import_incoming() == (2, {"Heat.1995.mkv"})
    files = api.called("POST", "command")[0][3]["files"]
    assert [f["movieId"] for f in files] == [1]
