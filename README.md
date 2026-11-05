# stremio-sync

Sends fully downloaded movies and episodes from Stremio's cache to the home server, then has
Radarr (movies) or Sonarr (episodes) add and import them (Jellyfin and Bazarr pick them up from there).

Movies go to `[server] incoming`, episodes to `[server] series_incoming`; leave the latter out to
skip series. From a season pack, only the episodes you streamed are sent, each one once it's complete.

Needs Python 3.11+ and Windows' built-in OpenSSH client. The command line needs no pip
packages; the tray app needs the ones in `requirements.txt`.

## Setup
1. Copy `config.toml.example` to `config.toml` and fill it in.
2. Copy your existing `sent.json` here, if you have one.
3. `py sync.py` for a dry run, `py sync.py --send` to send and import.

## Tray app
`py -m pip install -r requirements.txt`, then `pythonw sync.py --tray`. It watches Stremio's
cache instead of running on a timer:
- The moment a movie or episode appears in the cache, it hard-links the file into a staging folder
  (`[staging] dir`, by default `stremio-sync-staging` next to the cache, on the same drive).
  If Stremio clears its cache, the staged copy survives.
- When the download is complete it checks the file against the torrent's piece hashes,
  sends it, has Radarr or Sonarr import it, then deletes the staged copy.
- Menu: Running / Pause sending (keeps staging, holds uploads) / Stop watching, Sync now,
  Show downloads, Open log, Start at login, Quit.
- Show downloads has a **Movies** and a **Series** tab, each with two tabs of its own.
  **In progress** lists what the app is doing with each file (downloading, checking, waiting,
  sending, waiting for Radarr/Sonarr) and refreshes every 2 s; **Sent** lists what's been sent
  and whether it was imported. The **Add** tab downloads pasted links, see below.

## Adding downloads
Paste a link in Show downloads → **Add** and Stremio downloads it without the player open.
From there it goes like anything you'd watched: staged, checked, sent, imported.
- Links: a Stremio stream link (`http://127.0.0.1:11470/<hash>/<file>`, copy it from the
  player), a magnet, or an info hash. A season pack downloads every episode.
- Stremio (the desktop app) has to be open, since its streaming server does the downloading.
  If you close it, downloads wait and continue from where they were once you open it again.
  Quitting the tray app is fine too: the list is kept in `fetches.json`.
- `[stremio] max_downloads` (default 2) is how many download at once; the rest wait their turn.
  Stremio caps each torrent's speed, so you can watch something else meanwhile.
- Set the cache size to ∞ in Stremio's settings, so it doesn't evict half-downloaded files.
- Stop watching pauses these downloads too; Pause sending doesn't.

Turn on "Start at login" and delete the Task Scheduler task, if you had one.
The log is `sync.log` next to `config.toml` unless you pass `--log`.

## Scheduled runs (without the tray app)
Task Scheduler action: program `pythonw.exe`, arguments
`"C:\path\to\sync.py" --send --log "C:\path\to\sync.log"`.
The log gets only what was sent or imported, and any problems.

## Layout
```
sync.py                  Entry point
stremio_sync/
  cli.py                 Arguments, logging setup
  config.py              Loads config.toml
  pipeline.py            One full pass: scan, send, import
  state.py               sent.json: what's already been sent
  cache/                 Stremio's cache
    scan.py              Reads it, checks which movies and episodes are complete
    bencode.py           Decodes torrent metadata
    verify.py            Checks a file against the torrent's piece hashes
    staging.py           Hard links that keep files safe from Stremio's cache cleanup
  clients/               Other programs stremio-sync talks to
    stremio_server.py    Stremio's streaming server API (downloads torrents)
    home_server.py       ssh/scp upload to the home server
    arr/
      base.py            What the Radarr and Sonarr clients share
      radarr.py          Radarr API: add movies, import from incoming
      sonarr.py          Sonarr API: add series, import episodes from incoming
  fetch/                 Add tab: has Stremio's server download pasted links
    fetcher.py           The download list and its threads (fetches.json)
    links.py             Reads stream links and magnets, picks which files to download
    states.py            A download's saved states and how they're shown
    errors.py            Why a link can't be downloaded
  tray/                  The tray app
    app.py               Tray icon and menu
    engine.py            Worker: watch, stage, verify, send, import
    progress.py          What the worker is doing with each file, for the In progress tabs
    autostart.py         Start at login (Windows registry)
  window/                "Show downloads" window
    window.py            The window: tabs, status bar, refresh
    media_tab.py         Movies and Series tabs (In progress, Sent)
    add_tab.py           Add tab
    common.py            Table building and cell formatting the tabs share
tests/                   pytest suite, laid out like stremio_sync/
  helpers.py             Builds fake Stremio cache folders (torrent metadata, bitfield, data)
  fakes.py               Stand-ins for Stremio's server, Radarr/Sonarr, ssh/scp, the clock, threads
tools/
  preview_window.py      Opens the downloads window with made-up data
```

## Testing
`py -m pip install -r requirements-dev.txt`, then `py -m pytest`. Runs on Windows, macOS and Linux.
Nothing reaches the network or runs ssh: Stremio, Radarr, Sonarr and the server are faked, and a
test that tries to go out fails.

To see the downloads window without the tray app (or without Windows), run
`py tools/preview_window.py`. It's the real window filled with made-up rows; `--tab add`,
`--no-series` and `--empty` show other states. On macOS with Homebrew Python, tkinter needs `brew install python-tk@3.13` first.
