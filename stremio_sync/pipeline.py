"""One sync pass: scan the cache, send finished movies, import them into Radarr."""

import logging
import time

from .cache import CacheEntry, Skipped, scan_cache
from .config import Config
from .radarr import Radarr, RadarrError
from .state import SentLog
from .transfer import TransferError, send

log = logging.getLogger(__name__)


def fmt_duration(secs: float) -> str:
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def find_ready(cfg: Config, sent: SentLog) -> list[CacheEntry]:
    """Scan the cache (the table goes to the console only) and return complete, unsent entries."""
    log.debug("Scanning %s\n", cfg.cache_dir)
    ready = []
    for item in scan_cache(cfg.cache_dir):
        if isinstance(item, Skipped):
            log.debug("%7s  %s  %s", "skip", item.folder[:12], item.reason)
            continue
        if item in sent:
            status = "sent"
        elif item.complete:
            status = "READY"
            ready.append(item)
        else:
            status = f"{item.have / item.total:.1%}"
        log.debug("%7s  %s  %s  (%.2f GB)", status, item.infohash[:12], item.name, item.size / 1e9)
    return ready


def send_all(entries: list[CacheEntry], cfg: Config, sent: SentLog) -> int:
    """Send entries to the server, marking each in sent.json. Returns the number of failures."""
    if not entries:
        return 0
    if not cfg.ssh_target:
        log.error("No server set ([server] ssh in config.toml), can't send.")
        return 1

    failures = sent_bytes = 0
    run_start = time.monotonic()
    for n, entry in enumerate(entries, 1):
        log.info("\n[%d/%d] %s  (%.2f GB)", n, len(entries), entry.name, entry.size / 1e9)
        start = time.monotonic()
        try:
            outcome = send(entry, cfg.ssh_target, cfg.server_incoming)
        except TransferError as e:
            failures += 1
            log.error("  FAILED: %s", e)
            continue
        if outcome == "sent":
            secs = max(time.monotonic() - start, 0.001)
            sent_bytes += entry.size
            log.info("  done in %s  (%.1f MB/s)", fmt_duration(secs), entry.size / secs / 1e6)
        else:
            log.info("  already on server, marking as sent")
        sent.mark(entry)  # after each file, so a later failure doesn't lose progress

    log.info("\n%d sent, %d failed. %.2f GB in %s.", len(entries) - failures, failures,
             sent_bytes / 1e9, fmt_duration(time.monotonic() - run_start))
    return failures


def import_into_radarr(cfg: Config) -> int:
    if cfg.radarr is None:
        log.debug("\nRadarr not configured in config.toml, skipping import.")
        return 0
    try:
        return Radarr(cfg.radarr).import_incoming()
    except RadarrError as e:
        log.error("\nRadarr import failed: %s", e)
        return 1


def run(cfg: Config, send_files: bool) -> int:
    """One full pass. Returns the number of problems (0 = all good)."""
    sent = SentLog(cfg.state_file)
    ready = find_ready(cfg, sent)
    if not send_files:
        log.info("\n%d file(s) would be sent. Run with --send to copy them.", len(ready))
        return 0
    return send_all(ready, cfg, sent) + import_into_radarr(cfg)
