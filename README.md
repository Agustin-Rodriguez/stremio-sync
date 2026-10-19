# stremio-sync

Sends fully downloaded movies from Stremio's cache to the home server, then has Radarr
add and import them (Jellyfin and Bazarr pick them up from there).

Needs Python 3.11+ and Windows' built-in OpenSSH client. The command line needs no pip
packages; the tray app needs the ones in `requirements.txt`.

## Setup
1. Copy `config.toml.example` to `config.toml` and fill it in.
2. Copy your existing `sent.json` here, if you have one.
3. `py sync.py` for a dry run, `py sync.py --send` to send and import.

## Tray app
`py -m pip install -r requirements.txt`, then `pythonw sync.py --tray`. It watches Stremio's
cache instead of running on a timer:
- The moment a movie appears in the cache, it hard-links the file into a staging folder
  (`[staging] dir`, by default `stremio-sync-staging` next to the cache, on the same drive).
  If Stremio clears its cache, the staged copy survives.
- When the download is complete it checks the file against the torrent's piece hashes,
  sends it, has Radarr import it, then deletes the staged copy.
- Menu: Running / Pause sending (keeps staging, holds uploads) / Stop watching, Sync now,
  Show movies, Open log, Start at login, Quit.
- Show movies has two tabs. **In progress** lists what the app is doing with each movie
  (downloading, checking, waiting, sending, waiting for Radarr) and refreshes every 2 s;
  **Sent** lists what's been sent and whether Radarr imported it.

Turn on "Start at login" and delete the Task Scheduler task, if you had one.
The log is `sync.log` next to `config.toml` unless you pass `--log`.

## Scheduled runs (without the tray app)
Task Scheduler action: program `pythonw.exe`, arguments
`"C:\path\to\sync.py" --send --log "C:\path\to\sync.log"`.
The log gets only what was sent or imported, and any problems.

## Layout
| File | What it does |
|---|---|
| `sync.py` | Entry point |
| `stremio_sync/cli.py` | Arguments, logging setup |
| `stremio_sync/config.py` | Loads `config.toml` |
| `stremio_sync/pipeline.py` | One full pass: scan, send, import |
| `stremio_sync/cache.py` | Reads Stremio's cache, checks which movies are complete |
| `stremio_sync/bencode.py` | Decodes torrent metadata |
| `stremio_sync/state.py` | `sent.json`: what's already been sent |
| `stremio_sync/transfer.py` | ssh/scp upload to the server |
| `stremio_sync/radarr.py` | Radarr API: add movies, import from incoming |
| `stremio_sync/tray.py` | Tray icon and menu |
| `stremio_sync/engine.py` | Tray app worker: watch, stage, verify, send, import |
| `stremio_sync/progress.py` | What the worker is doing with each movie, for the In progress tab |
| `stremio_sync/staging.py` | Hard links that keep movies safe from Stremio's cache cleanup |
| `stremio_sync/verify.py` | Checks a file against the torrent's piece hashes |
| `stremio_sync/window.py` | "Show movies" window (In progress, Sent) |
| `stremio_sync/autostart.py` | Start at login (registry Run key) |
