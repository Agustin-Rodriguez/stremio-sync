"""Command-line entry point."""

import argparse
import dataclasses
import logging
import sys
from pathlib import Path

from .config import CONFIG_FILE, ConfigError, load_config
from .pipeline import run


class _FileFormatter(logging.Formatter):
    """Timestamped lines, without the blank-line spacing used on the console."""
    def formatMessage(self, record):
        record.message = record.message.lstrip("\n")
        return super().formatMessage(record)


def setup_logging(log_file: Path | None):
    logger = logging.getLogger("stremio_sync")
    logger.setLevel(logging.DEBUG)
    if sys.stdout is not None:  # None under pythonw.exe
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(console)
    if log_file:
        # The log only gets INFO and up: what was sent/imported and any problems, not the scan table.
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(_FileFormatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
        logger.addHandler(fh)
    return logger


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="sync.py",
        description="Send finished Stremio downloads to the home server and import them into Radarr.",
    )
    ap.add_argument("--send", action="store_true", help="actually copy READY files and run the Radarr import")
    ap.add_argument("--config", type=Path, default=CONFIG_FILE, help="config file (default: config.toml next to sync.py)")
    ap.add_argument("--log", type=Path, metavar="FILE", help="also append results to FILE, with timestamps")
    ap.add_argument("--cache-dir", type=Path, help="override [stremio] cache_dir")
    ap.add_argument("--server", help="override [server] ssh")
    ap.add_argument("--dest", help="override [server] incoming")
    args = ap.parse_args(argv)

    log = setup_logging(args.log)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        log.error("%s", e)
        sys.exit(2)

    overrides = {"cache_dir": args.cache_dir, "ssh_target": args.server, "server_incoming": args.dest}
    cfg = dataclasses.replace(cfg, **{k: v for k, v in overrides.items() if v})

    if not cfg.cache_dir.is_dir():
        log.error("Cache folder not found: %s (set [stremio] cache_dir in config.toml)", cfg.cache_dir)
        sys.exit(2)

    sys.exit(1 if run(cfg, send_files=args.send) else 0)
