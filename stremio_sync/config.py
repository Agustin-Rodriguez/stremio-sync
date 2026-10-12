"""Settings, loaded from config.toml (never overwritten by updates)."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .cache import default_cache_dir

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = PROJECT_DIR / "config.toml"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class RadarrConfig:
    url: str
    api_key: str
    incoming: str             # incoming folder as Radarr sees it (inside Docker)
    quality_profile: str | None
    monitor_new: bool


@dataclass(frozen=True)
class Config:
    ssh_target: str | None    # e.g. "agustin@192.168.0.100"
    server_incoming: str      # incoming folder as the server sees it
    cache_dir: Path
    state_file: Path          # sent.json, kept next to config.toml
    radarr: RadarrConfig | None  # None = skip the import step


def load_config(path: Path = CONFIG_FILE) -> Config:
    if not path.is_file():
        raise ConfigError(f"No config file at {path}. Copy config.toml.example to config.toml and fill it in.")
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path.name}: {e}") from e

    server = raw.get("server", {})
    stremio = raw.get("stremio", {})
    r = raw.get("radarr", {})

    radarr = None
    if r.get("api_key") and r.get("incoming"):
        radarr = RadarrConfig(
            url=r.get("url", "http://localhost:7878"),
            api_key=r["api_key"],
            incoming=r["incoming"].rstrip("/"),
            quality_profile=r.get("quality_profile") or None,
            monitor_new=bool(r.get("monitor_new", True)),
        )

    return Config(
        ssh_target=server.get("ssh") or None,
        server_incoming=server.get("incoming", "/mnt/media_1/incoming").rstrip("/"),
        cache_dir=Path(stremio["cache_dir"]) if stremio.get("cache_dir") else default_cache_dir(),
        state_file=path.resolve().parent / "sent.json",
        radarr=radarr,
    )
