---
name: Project context
alwaysApply: true
---

# stremio-sync
Copies fully downloaded movies from Stremio's torrent cache (Windows) to a home
server over ssh/scp, then asks Radarr to import them. Movies only; series are skipped.

## Hard rules
- Python 3.11+, **standard library only**. Never add dependencies.
- Runs under `pythonw.exe` from Task Scheduler, so `sys.stdout` can be `None`.
  Never `print`; use `logging.getLogger(__name__)` with %-style args, not f-strings.
- DEBUG goes to the console only; INFO and up also go to the log file.
- ssh/scp through `subprocess` (Windows' built-in OpenSSH).
- One failed movie must not stop the run. Raise the module's own exception
  (`ConfigError`, `TransferError`, `RadarrError`); `pipeline.py` catches, logs, counts.
- Save progress after each file (`sent.mark(entry)`). Write files atomically
  (temp file, then `os.replace`).
- Never edit `config.toml`, `sent.json` or `*.log`. Change `config.toml.example`
  and keep it in sync with `config.py`.

## Layout
Code lives in `stremio_sync/`, entry point is `sync.py`.
- `cli.py`: arguments, logging
- `config.py`: loads `config.toml`
- `pipeline.py`: one pass (scan, send, import)
- `cache.py`: reads Stremio's cache, skips series
- `bencode.py`: decodes torrent metadata
- `state.py`: `sent.json`, what's already sent
- `transfer.py`: ssh/scp upload
- `radarr.py`: Radarr API

## Style
Small modules, short functions, type hints, one-line module docstrings. Sparse
comments that explain why. Short log messages, e.g. `"  FAILED: %s"`.