# stremio-sync

Sends fully downloaded movies from Stremio's cache to the home server, then has Radarr
add and import them (Jellyfin and Bazarr pick them up from there).

Needs Python 3.11+ and Windows' built-in OpenSSH client. No pip packages.

## Setup
1. Copy `config.toml.example` to `config.toml` and fill it in.
2. Copy your existing `sent.json` here, if you have one.
3. `py sync.py` for a dry run, `py sync.py --send` to send and import.

## Scheduled runs
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
