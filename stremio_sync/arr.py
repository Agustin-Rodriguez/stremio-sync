"""What Radarr and Sonarr share: their v3 API, commands, and defaults for things we add."""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import ArrConfig

log = logging.getLogger(__name__)


class ArrError(Exception):
    """Radarr or Sonarr unreachable or returned an error."""


class NoMatch(Exception):
    """A file we can't confidently match to a movie or episode."""


class ArrClient:
    name = "*arr"
    error = ArrError

    def __init__(self, cfg: ArrConfig):
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
            raise self.error(f"{method} {path}: HTTP {e.code} {detail}") from e
        except urllib.error.URLError as e:
            raise self.error(f"can't reach {self.name} at {self.cfg.url}: {e.reason}") from e
        return json.loads(raw) if raw else None

    def add_defaults(self) -> tuple[str, int]:
        """(root folder, quality profile id) for things we add. Fetched once, only if needed."""
        if self._add_defaults is None:
            roots = self.request("GET", "rootfolder") or []
            if not roots:
                raise self.error(f"{self.name} has no root folder configured (Settings -> Media Management)")
            profiles = self.request("GET", "qualityprofile") or []
            wanted = self.cfg.quality_profile
            if wanted:
                profile = next((p for p in profiles if p["name"].lower() == wanted.lower()), None)
                if profile is None:
                    raise self.error(f"no {self.name} quality profile named {wanted!r}")
            elif profiles:
                profile = profiles[0]
            else:
                raise self.error(f"{self.name} has no quality profiles")
            self._add_defaults = (roots[0]["path"], profile["id"])
        return self._add_defaults

    def wait_for_command(self, cmd_id, timeout=300) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cmd = self.request("GET", f"command/{cmd_id}") or {}
            if cmd.get("status") in ("completed", "failed", "aborted", "cancelled"):
                return cmd["status"]
            time.sleep(2)
        return "timed out"

    def waiting(self) -> tuple[dict, list[dict]]:
        """(listing params, items) for what's in incoming, not yet in the library."""
        listing = {"folder": self.cfg.incoming, "filterExistingFiles": "true"}
        return listing, self.request("GET", "manualimport", listing) or []

    def run_import(self, files: list[dict], listing: dict) -> tuple[int, set[str]]:
        """Import `files` (ManualImport entries) and check which left incoming. Returns (problems, imported names)."""
        problems, imported = 0, set()
        cmd = self.request("POST", "command", body={"name": "ManualImport", "files": files, "importMode": "move"})
        status = self.wait_for_command(cmd["id"])
        # A file that's gone from incoming was moved into the library: that's the real success check.
        remaining = {i["path"] for i in (self.request("GET", "manualimport", listing) or [])}
        for f in files:
            name = f["path"].rsplit("/", 1)[-1]
            if f["path"] in remaining:
                problems += 1
                log.error("  FAILED    %s (import %s; see %s -> System -> Logs)", name, status, self.name)
            else:
                imported.add(name)
                log.info("  imported  %s", name)
        return problems, imported
