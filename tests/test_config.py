from pathlib import Path

import pytest

from stremio_sync.cache.scan import EPISODE, MOVIE, CacheEntry
from stremio_sync.config import ConfigError, load_config

EXAMPLE = Path(__file__).resolve().parent.parent / "config.toml.example"


def write(tmp_path, text):
    p = tmp_path / "config.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="No config file"):
        load_config(tmp_path / "config.toml")


def test_bad_toml(tmp_path):
    with pytest.raises(ConfigError, match="config.toml"):
        load_config(write(tmp_path, "[server\n"))


def test_empty_file_gives_defaults(tmp_path):
    cfg = load_config(write(tmp_path, ""))
    assert cfg.ssh_target is None
    assert cfg.server_incoming == "/mnt/media_1/incoming"
    assert cfg.server_series_incoming is None
    assert cfg.state_file == tmp_path.resolve() / "sent.json"
    assert cfg.staging_dir is None
    assert cfg.radarr is None and cfg.sonarr is None
    assert cfg.stremio_server == "http://127.0.0.1:11470"
    assert cfg.max_downloads == 2


def test_the_example_file_loads(tmp_path):
    cfg = load_config(write(tmp_path, EXAMPLE.read_text(encoding="utf-8")))
    assert cfg.ssh_target == "youruser@192.168.0.100"
    assert cfg.radarr is None  # api_key is empty in the example


def test_full_config(tmp_path):
    cfg = load_config(write(tmp_path, r"""
[server]
ssh = "me@nas"
incoming = "/in/"
series_incoming = "/tv/"
[stremio]
cache_dir = 'D:\Cache'
server = "http://host:1/"
max_downloads = 4
[staging]
dir = "/stage"
[radarr]
api_key = "k"
incoming = "/data/in/"
[sonarr]
url = "http://s"
api_key = "k2"
incoming = "/data/tv"
quality_profile = ""
monitor_new = false
"""))
    assert (cfg.server_incoming, cfg.server_series_incoming) == ("/in", "/tv")
    assert cfg.cache_dir == Path(r"D:\Cache")
    assert cfg.stremio_server == "http://host:1"
    assert cfg.max_downloads == 4
    assert cfg.staging_dir == Path("/stage")
    assert cfg.radarr.url == "http://localhost:7878" and cfg.radarr.incoming == "/data/in"
    assert cfg.sonarr.quality_profile is None and cfg.sonarr.monitor_new is False


def test_arr_needs_api_key_and_incoming(tmp_path):
    cfg = load_config(write(tmp_path, '[radarr]\napi_key = "k"\n'))
    assert cfg.radarr is None


@pytest.mark.parametrize("value", ["0", "-1", "true", '"3"', "1.5"])
def test_bad_max_downloads(tmp_path, value):
    with pytest.raises(ConfigError, match="max_downloads"):
        load_config(write(tmp_path, f"[stremio]\nmax_downloads = {value}\n"))


def test_dest_and_arr_for_each_kind(make_config):
    cfg = make_config()
    movie = CacheEntry("a", 0, Path("0"), "m.mkv", 1, 1, 1, MOVIE)
    episode = CacheEntry("a", 1, Path("1"), "s01e01.mkv", 1, 1, 1, EPISODE)
    assert cfg.dest_for(movie) == "/srv/incoming" and cfg.arr_for(movie) is cfg.radarr
    assert cfg.dest_for(episode) == "/srv/incoming-tv" and cfg.arr_for(episode) is cfg.sonarr
    assert make_config(server_series_incoming=None).dest_for(episode) is None
