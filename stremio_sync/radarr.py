"""Radarr API: add movies it doesn't know yet, and import files from incoming."""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import RadarrConfig

log = logging.getLogger(__name__)


class RadarrError(Exception):
    """Radarr unreachable or returned an error."""


class NoMatch(Exception):
    """A file we can't confidently match to a movie."""


class Radarr:
    def __init__(self, cfg: RadarrConfig):
        self.cfg = cfg
        self._add_defaults = None

    def request(self, method, path, params=None, body=None):
        url = f"{self.cfg.url.rstrip('/')}/api/v3/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"X-Api-Key": self.cfg.api_key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode(errors="replace")
            raise RadarrError(f"{method} {path}: HTTP {e.code} {detail}") from e
        except urllib.error.URLError as e:
            raise RadarrError(f"can't reach Radarr at {self.cfg.url}: {e.reason}") from e
        return json.loads(raw) if raw else None

    def add_defaults(self) -> tuple[str, int]:
        """(root folder, quality profile id) for movies we add. Fetched once, only if needed."""
        if self._add_defaults is None:
            roots = self.request("GET", "rootfolder") or []
            if not roots:
                raise RadarrError("Radarr has no root folder configured (Settings -> Media Management)")
            profiles = self.request("GET", "qualityprofile") or []
            wanted = self.cfg.quality_profile
            if wanted:
                profile = next((p for p in profiles if p["name"].lower() == wanted.lower()), None)
                if profile is None:
                    raise RadarrError(f"no Radarr quality profile named {wanted!r}")
            elif profiles:
                profile = profiles[0]
            else:
                raise RadarrError("Radarr has no quality profiles")
            self._add_defaults = (roots[0]["path"], profile["id"])
        return self._add_defaults

    def find_or_add_movie(self, filename: str) -> tuple[dict, bool]:
        """(movie, added) for a file name, adding the movie to Radarr if needed. Raises NoMatch."""
        parsed = self.request("GET", "parse", {"title": filename}) or {}
        if parsed.get("movie"):
            return parsed["movie"], False

        info = parsed.get("parsedMovieInfo") or {}
        titles = info.get("movieTitles") or ([info["movieTitle"]] if info.get("movieTitle") else [])
        year = info.get("year") or 0
        if not titles or not year:
            raise NoMatch("couldn't read a title and year from the file name")

        results = self.request("GET", "movie/lookup", {"term": f"{titles[0]} {year}"}) or []
        # Exact year only: a wrong match files the movie under the wrong title, which is worse than skipping.
        match = next((m for m in results if m.get("year") == year), None)
        if match is None:
            raise NoMatch(f"no exact match for '{titles[0]} ({year})', add it in Radarr manually")

        existing = self.request("GET", "movie", {"tmdbId": match["tmdbId"]}) or []
        if existing:
            return existing[0], False

        root, profile_id = self.add_defaults()
        body = dict(match)
        body.pop("id", None)
        body.update(
            qualityProfileId=profile_id,
            rootFolderPath=root,
            monitored=self.cfg.monitor_new,
            minimumAvailability="released",
            addOptions={"searchForMovie": False},  # we already have the file; don't go looking for one
        )
        return self.request("POST", "movie", body=body), True

    def wait_for_command(self, cmd_id, timeout=300) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cmd = self.request("GET", f"command/{cmd_id}") or {}
            if cmd.get("status") in ("completed", "failed", "aborted", "cancelled"):
                return cmd["status"]
            time.sleep(2)
        return "timed out"

    def import_incoming(self) -> tuple[int, set[str]]:
        """Import every movie file in incoming, this run's and leftovers. Returns (problems, imported file names)."""
        listing = {"folder": self.cfg.incoming, "filterExistingFiles": "true"}
        items = self.request("GET", "manualimport", listing) or []
        if not items:
            log.debug("\nRadarr: nothing waiting in %s", self.cfg.incoming)
            return 0, set()
        log.info("\nRadarr: %d file(s) waiting in %s", len(items), self.cfg.incoming)

        files, problems, imported = [], 0, set()
        for item in items:
            name = item["path"].rsplit("/", 1)[-1]
            try:
                movie, added = item.get("movie"), False
                if not movie:
                    movie, added = self.find_or_add_movie(name)
            except NoMatch as e:
                problems += 1
                log.warning("  skip      %s: %s", name, e)
                continue
            label = f"{movie['title']} ({movie.get('year')})"
            if movie.get("hasFile"):
                problems += 1
                log.warning("  skip      %s: %s already has a file in Radarr, leaving this one in incoming", name, label)
                continue
            if not item.get("quality"):
                problems += 1
                log.warning("  skip      %s: Radarr couldn't determine its quality", name)
                continue
            log.info("  %-9s %s -> %s", "added" if added else "matched", name, label)
            files.append({
                "path": item["path"],
                "movieId": movie["id"],
                "quality": item["quality"],
                "languages": item.get("languages") or [],
                "releaseGroup": item.get("releaseGroup") or "",
            })

        if files:
            cmd = self.request("POST", "command", body={"name": "ManualImport", "files": files, "importMode": "move"})
            status = self.wait_for_command(cmd["id"])
            # A file that's gone from incoming was moved into the library: that's the real success check.
            remaining = {i["path"] for i in (self.request("GET", "manualimport", listing) or [])}
            for f in files:
                name = f["path"].rsplit("/", 1)[-1]
                if f["path"] in remaining:
                    problems += 1
                    log.error("  FAILED    %s (import %s; see Radarr -> System -> Logs)", name, status)
                else:
                    imported.add(name)
                    log.info("  imported  %s", name)
        return problems, imported
