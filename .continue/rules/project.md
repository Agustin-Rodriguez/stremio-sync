---
name: Project context
alwaysApply: true
---

# stremio-sync
Copies fully downloaded movies and episodes from Stremio's torrent cache (Windows) to a home
server over ssh/scp (movies to `[server] incoming`, episodes to `[server] series_incoming`),
then asks Radarr (movies) or Sonarr (episodes) to import them. Series are skipped when
`series_incoming` isn't set. From a season pack, each streamed episode is its own entry.
Runs either as a one-shot CLI or as a tray app (`--tray`) that watches the cache.

## Hard rules
- Python 3.11+, standard library plus the packages in `requirements.txt` (pystray, Pillow,
  watchdog). Only the tray modules import them; the CLI must keep working without them.
  Don't add more without asking.
- Runs under `pythonw.exe` (tray app, Task Scheduler), so `sys.stdout` can be `None`.
  Never `print`; use `logging.getLogger(__name__)` with %-style args, not f-strings.
- DEBUG goes to the console only; INFO and up also go to the log file.
- ssh/scp through `subprocess` (Windows' built-in OpenSSH).
- One failed file must not stop the run. Raise the module's own exception
  (`ConfigError`, `TransferError`, `RadarrError`, `SonarrError`, `StagingError`); `pipeline.py` and
  `engine.py` catch, log, count.
- Tray threading: tkinter owns the main thread, pystray runs detached, the engine has its
  own worker thread. Other threads hand tk work to the main thread through the tray's queue.
- Save progress after each file (`sent.mark(entry)`). Write files atomically
  (temp file, then `os.replace`).
- Never edit `config.toml`, `sent.json` or `*.log`. Change `config.toml.example`
  and keep it in sync with `config.py`.

## Layout
Code lives in `stremio_sync/`, entry point is `sync.py`.
- `cli.py`: arguments, logging
- `config.py`: loads `config.toml`
- `pipeline.py`: one pass (scan, send, import)
- `cache.py`: reads Stremio's cache; one entry per movie or streamed episode
- `bencode.py`: decodes torrent metadata
- `state.py`: `sent.json`, what's already sent
- `transfer.py`: ssh/scp upload
- `arr.py`: shared Radarr/Sonarr client; `radarr.py`: Radarr API; `sonarr.py`: Sonarr API
- `tray.py`: tray icon and menu; `window.py`: "Show downloads" window; `autostart.py`: Run key
- `engine.py`: tray worker (watch, stage, verify, send, import)
- `progress.py`: per-file state for the window's In progress tabs
- `staging.py`: hard links that outlive Stremio's cache cleanup; `verify.py`: piece hashes

## Style
Small modules, short functions, type hints, one-line module docstrings. Sparse
comments that explain why. Short log messages, e.g. `"  FAILED: %s"`.