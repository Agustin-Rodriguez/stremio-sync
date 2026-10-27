"""Settings, loaded from config.toml (never overwritten by updates)."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .cache import MOVIE, CacheEntry, default_cache_dir

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = PROJECT_DIR / "config.toml"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class ArrConfig:
    """Radarr or Sonarr."""
    url: str
    api_key: str
    incoming: str             # incoming folder as Radarr/Sonarr sees it (inside Docker)
    quality_profile: str | None
    monitor_new: bool


@dataclass(frozen=True)
class Config:
    ssh_target: str | None    # e.g. "agustin@192.168.0.100"
    server_incoming: str      # incoming folder as the server sees it
    server_series_incoming: str | None  # same, for episodes; None = skip series
    cache_dir: Path
    state_file: Path          # sent.json, kept next to config.toml
    staging_dir: Path | None  # tray app's hard links; None = next to the cache folder
    radarr: ArrConfig | None  # None = skip the import step
    sonarr: ArrConfig | None

    def dest_for(self, entry: CacheEntry) -> str | None:
        """Where on the server an entry goes. None = we don't sync its kind."""
        return self.server_incoming if entry.kind == MOVIE else self.server_series_incoming

    def arr_for(self, entry: CacheEntry) -> ArrConfig | None:
        return self.radarr if entry.kind == MOVIE else self.sonarr


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
    staging = raw.get("staging", {})

    return Config(
        ssh_target=server.get("ssh") or None,
        server_incoming=server.get("incoming", "/mnt/media_1/incoming").rstrip("/"),
        server_series_incoming=server["series_incoming"].rstrip("/") if server.get("series_incoming") else None,
        cache_dir=Path(stremio["cache_dir"]) if stremio.get("cache_dir") else default_cache_dir(),
        state_file=path.resolve().parent / "sent.json",
        staging_dir=Path(staging["dir"]) if staging.get("dir") else None,
        radarr=_arr(raw.get("radarr", {}), "http://localhost:7878"),
        sonarr=_arr(raw.get("sonarr", {}), "http://localhost:8989"),
    )


def _arr(section: dict, default_url: str) -> ArrConfig | None:
    if not (section.get("api_key") and section.get("incoming")):
        return None
    return ArrConfig(
        url=section.get("url", default_url),
        api_key=section["api_key"],
        incoming=section["incoming"].rstrip("/"),
        quality_profile=section.get("quality_profile") or None,
        monitor_new=bool(section.get("monitor_new", True)),
    )
