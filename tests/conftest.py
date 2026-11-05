import dataclasses
import sys
import types
from pathlib import Path

import pytest

# stremio_sync.tray imports autostart, which needs Windows' winreg. Tests never touch the registry,
# so on macOS/Linux an empty stand-in lets the tray package import.
if sys.platform != "win32" and "winreg" not in sys.modules:
    sys.modules["winreg"] = types.ModuleType("winreg")

from stremio_sync.clients.arr.base import ArrConfig  # noqa: E402
from stremio_sync.config import Config  # noqa: E402


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    d = tmp_path / "stremio-cache"
    d.mkdir()
    return d


@pytest.fixture
def make_config(tmp_path: Path, cache_dir: Path):
    """A Config pointing everything at tmp_path; pass fields to override."""
    def make(**overrides) -> Config:
        base = Config(
            ssh_target="me@server",
            server_incoming="/srv/incoming",
            server_series_incoming="/srv/incoming-tv",
            cache_dir=cache_dir,
            state_file=tmp_path / "sent.json",
            staging_dir=tmp_path / "staging",
            radarr=ArrConfig("http://radarr", "rkey", "/data/incoming", None, True),
            sonarr=ArrConfig("http://sonarr", "skey", "/data/incoming-tv", None, True),
            stremio_server="http://127.0.0.1:11470",
            max_downloads=2,
        )
        return dataclasses.replace(base, **overrides)
    return make


@pytest.fixture
def cfg(make_config) -> Config:
    return make_config()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Every outside service is faked; a test that reaches for the network is a bug in the test."""
    import socket
    import subprocess
    import urllib.request

    def blocked(*args, **kwargs):
        raise AssertionError(f"test tried to use the network or run a program: {args[:1]}")
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(subprocess, "run", blocked)
