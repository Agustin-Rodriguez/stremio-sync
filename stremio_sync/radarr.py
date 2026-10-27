"""Radarr API: add movies it doesn't know yet, and import files from incoming."""

import logging

from .arr import ArrClient, ArrError, NoMatch

log = logging.getLogger(__name__)


class RadarrError(ArrError):
    """Radarr unreachable or returned an error."""


class Radarr(ArrClient):
    name = "Radarr"
    error = RadarrError

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

    def import_incoming(self) -> tuple[int, set[str]]:
        """Import every movie file in incoming, this run's and leftovers. Returns (problems, imported file names)."""
        listing, items = self.waiting()
        if not items:
            log.debug("\nRadarr: nothing waiting in %s", self.cfg.incoming)
            return 0, set()
        log.info("\nRadarr: %d file(s) waiting in %s", len(items), self.cfg.incoming)

        files, problems = [], 0
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

        if not files:
            return problems, set()
        failed, imported = self.run_import(files, listing)
        return problems + failed, imported
