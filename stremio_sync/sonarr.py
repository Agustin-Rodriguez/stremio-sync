"""Sonarr API: add series it doesn't know yet, and import episode files from incoming."""

import logging
import re
import time

from .arr import ArrClient, ArrError, NoMatch

log = logging.getLogger(__name__)

EPISODES_TIMEOUT = 120  # seconds to wait for a newly added series to get its episode list


class SonarrError(ArrError):
    """Sonarr unreachable or returned an error."""


def _norm(title: str) -> str:
    # Sonarr tells same-named shows apart as "Doctor Who (2005)"; the year is compared separately.
    title = re.sub(r"\s*\(\d{4}\)$", "", title)
    return re.sub(r"[^a-z0-9]", "", title.lower())


class Sonarr(ArrClient):
    name = "Sonarr"
    error = SonarrError

    def language_profile(self) -> int | None:
        """Sonarr v3 needs one to add a series; v4 dropped them."""
        try:
            profiles = self.request("GET", "languageprofile") or []
        except SonarrError:
            return None
        return profiles[0]["id"] if profiles else None

    def find_or_add_series(self, filename: str) -> tuple[dict, list[dict], bool]:
        """(series, episodes, added) for a file name, adding the series to Sonarr if needed. Raises NoMatch."""
        parsed = self.request("GET", "parse", {"title": filename}) or {}
        info = parsed.get("parsedEpisodeInfo") or {}
        if parsed.get("series"):
            return parsed["series"], self.episodes(parsed["series"], info, parsed.get("episodes")), False

        title_info = info.get("seriesTitleInfo") or {}
        # "title" keeps the year from the file name ("Show 2005"), which no lookup result is called.
        title = title_info.get("titleWithoutYear") or title_info.get("title") or info.get("seriesTitle")
        year = title_info.get("year") or 0
        if not title:
            raise NoMatch("couldn't read a series title from the file name")

        results = self.request("GET", "series/lookup", {"term": title}) or []
        # Exact title only (and year, if the name has one): a wrong match files the episode under
        # the wrong show, which is worse than skipping.
        matches = [s for s in results if _norm(s.get("title", "")) == _norm(title)]
        if year:
            matches = [s for s in matches if s.get("year") == year]
        if len(matches) != 1:
            what = "no exact match" if not matches else f"{len(matches)} shows match"
            raise NoMatch(f"{what} for '{title}', add it in Sonarr manually")
        match = matches[0]

        existing = self.request("GET", "series", {"tvdbId": match["tvdbId"]}) or []
        if existing:
            return existing[0], self.episodes(existing[0], info), False

        root, profile_id = self.add_defaults()
        body = dict(match)
        body.pop("id", None)
        body.update(
            qualityProfileId=profile_id,
            rootFolderPath=root,
            monitored=self.cfg.monitor_new,
            seasonFolder=True,
            # We already have the file; don't go looking for it or for the rest of the show.
            addOptions={"monitor": "none", "searchForMissingEpisodes": False},
        )
        language = self.language_profile()
        if language is not None:
            body["languageProfileId"] = language
        series = self.request("POST", "series", body=body)
        return series, self.episodes(series, info, wait=True), True

    def episodes(self, series: dict, info: dict, parsed: list[dict] | None = None,
                 wait: bool = False) -> list[dict]:
        """The episodes a file holds, from the parse result or the series' episode list. Raises NoMatch."""
        if parsed:
            return parsed
        season, numbers = info.get("seasonNumber"), info.get("episodeNumbers") or []
        if season is None or not numbers:
            raise NoMatch("couldn't read a season and episode number from the file name")
        deadline = time.monotonic() + (EPISODES_TIMEOUT if wait else 0)
        while True:
            # A series we just added fills in its episode list in the background.
            listing = self.request("GET", "episode", {"seriesId": series["id"], "seasonNumber": season}) or []
            found = [e for e in listing if e.get("episodeNumber") in numbers]
            if len(found) == len(numbers):
                return found
            if time.monotonic() >= deadline:
                raise NoMatch(f"{series['title']} has no S{season:02d}E{numbers[0]:02d} in Sonarr")
            time.sleep(2)

    def import_incoming(self) -> tuple[int, set[str]]:
        """Import every episode file in incoming, this run's and leftovers. Returns (problems, imported file names)."""
        listing, items = self.waiting()
        if not items:
            log.debug("\nSonarr: nothing waiting in %s", self.cfg.incoming)
            return 0, set()
        log.info("\nSonarr: %d file(s) waiting in %s", len(items), self.cfg.incoming)

        files, problems = [], 0
        for item in items:
            name = item["path"].rsplit("/", 1)[-1]
            try:
                series, episodes, added = item.get("series"), item.get("episodes"), False
                if not series or not episodes:
                    series, episodes, added = self.find_or_add_series(name)
            except NoMatch as e:
                problems += 1
                log.warning("  skip      %s: %s", name, e)
                continue
            label = f"{series['title']} " + ", ".join(
                f"S{e['seasonNumber']:02d}E{e['episodeNumber']:02d}" for e in episodes)
            if any(e.get("hasFile") for e in episodes):
                problems += 1
                log.warning("  skip      %s: %s already has a file in Sonarr, leaving this one in incoming", name, label)
                continue
            if not item.get("quality"):
                problems += 1
                log.warning("  skip      %s: Sonarr couldn't determine its quality", name)
                continue
            log.info("  %-9s %s -> %s", "added" if added else "matched", name, label)
            files.append({
                "path": item["path"],
                "seriesId": series["id"],
                "episodeIds": [e["id"] for e in episodes],
                "quality": item["quality"],
                "languages": item.get("languages") or [],
                "releaseGroup": item.get("releaseGroup") or "",
            })

        if not files:
            return problems, set()
        if self.cfg.monitor_new:
            # Bazarr only fetches subtitles for monitored episodes by default.
            ids = [i for f in files for i in f["episodeIds"]]
            self.request("PUT", "episode/monitor", body={"episodeIds": ids, "monitored": True})
        failed, imported = self.run_import(files, listing)
        return problems + failed, imported
