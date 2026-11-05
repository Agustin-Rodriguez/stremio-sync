"""Open the real "Show downloads" window with made-up data: no Windows, Stremio or server needed.

    python tools/preview_window.py                     # look and click around
    python tools/preview_window.py --tab add           # start on the Add tab
    python tools/preview_window.py --no-series         # as if [server] series_incoming isn't set
    python tools/preview_window.py --empty             # nothing in progress, sent or added
    
The window is the app's own code (stremio_sync.window); only the data behind it is fake.
On macOS it draws with the Aqua theme, so widgets look a little different than on Windows.
"""

import argparse
import json
import sys
import tempfile
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stremio_sync.cache.scan import EPISODE, MOVIE  # noqa: E402
from stremio_sync.fetch.errors import FetchError  # noqa: E402
from stremio_sync.fetch.links import parse_link  # noqa: E402
from stremio_sync.fetch.states import DONE, DOWNLOADING, FAILED, FINISHED, QUEUED  # noqa: E402
from stremio_sync.window import DownloadsWindow  # noqa: E402

GB = 1_000_000_000

SENT = {
    "a1:0": {"name": "Dune.Part.Two.2024.2160p.WEB-DL.DDP5.1.Atmos.mkv", "size": int(18.4 * GB),
             "kind": MOVIE, "sent_at": "2026-11-02T21:14:09", "imported_at": "2026-11-02T21:16:40"},
    "a2:0": {"name": "Past.Lives.2023.1080p.BluRay.x264.mkv", "size": int(9.1 * GB),
             "kind": MOVIE, "sent_at": "2026-11-04T19:02:51"},
    "a3:0": {"name": "Heat.1995.Remastered.1080p.BluRay.mkv", "sent_at": "2025-03-10T08:00:00"},  # pre-size record
    "b1:3": {"name": "Severance.S02E04.1080p.ATVP.WEB-DL.mkv", "size": int(2.3 * GB),
             "kind": EPISODE, "sent_at": "2026-11-03T23:40:12", "imported_at": "2026-11-03T23:41:05"},
    "b1:4": {"name": "Severance.S02E05.1080p.ATVP.WEB-DL.mkv", "size": int(2.2 * GB),
             "kind": EPISODE, "sent_at": "2026-11-05T00:12:44"},
}

PROGRESS = [
    {"key": "c1:0", "name": "Oppenheimer.2023.1080p.BluRay.DTS.mkv", "size": int(14.2 * GB), "kind": MOVIE,
     "state": "Sending", "progress": "3m12s"},
    {"key": "c2:0", "name": "Perfect.Days.2023.1080p.WEB-DL.mkv", "size": int(5.6 * GB), "kind": MOVIE,
     "state": "Checking", "progress": ""},
    {"key": "c3:0", "name": "The.Zone.of.Interest.2023.1080p.mkv", "size": int(7.0 * GB), "kind": MOVIE,
     "state": "Waiting to send (retrying in 4m10s)", "progress": ""},
    {"key": "c4:0", "name": "Anatomy.of.a.Fall.2023.1080p.mkv", "size": int(8.3 * GB), "kind": MOVIE,
     "state": "Downloading", "progress": "62%"},
    {"key": "c5:0", "name": "Aftersun.2022.1080p.WEB-DL.mkv", "size": int(4.4 * GB), "kind": MOVIE,
     "state": "Stalled", "progress": "18%"},
    {"key": "d1:5", "name": "Severance.S02E06.1080p.ATVP.WEB-DL.mkv", "size": int(2.4 * GB), "kind": EPISODE,
     "state": "Damaged, waiting for Stremio", "progress": ""},
    {"key": "d1:6", "name": "Severance.S02E07.1080p.ATVP.WEB-DL.mkv", "size": int(2.1 * GB), "kind": EPISODE,
     "state": "Downloading", "progress": "35%"},
    {"key": "d2:0", "name": "Shogun.2024.S01E01.1080p.mkv", "size": int(3.0 * GB), "kind": EPISODE,
     "state": "Waiting for Sonarr", "progress": ""},
]

FETCHES = [
    {"key": "f1", "name": "The.Holdovers.2023.1080p.WEB-DL.mkv", "size": int(6.2 * GB), "phase": DOWNLOADING,
     "state": "Downloading", "progress": "41%  3.8 MB/s"},
    {"key": "f2", "name": "Slow Horses S04", "size": int(12.8 * GB), "phase": DOWNLOADING,
     "state": "Downloading episode 3 of 6", "progress": "37%  2.1 MB/s"},
    {"key": "f3", "name": "Fallen Leaves 2023", "size": None, "phase": DOWNLOADING,
     "state": "Getting torrent info", "progress": ""},
    {"key": "f4", "name": "Monster.2023.1080p.BluRay.mkv", "size": None, "phase": QUEUED,
     "state": "Queued", "progress": ""},
    {"key": "f5", "name": "Godzilla.Minus.One.2023.1080p.mkv", "size": int(5.1 * GB), "phase": DONE,
     "state": "Done", "progress": "100%"},
    {"key": "f6", "name": "Some.Sample.Pack", "size": None, "phase": FAILED,
     "state": "Failed: several video files and no episode numbers, skipping", "progress": ""},
]


class FakeFetcher:
    """What the Add tab calls on the real Fetcher: snapshot, add, cancel, clear_finished."""

    def __init__(self, rows: list[dict]):
        self.rows = [dict(r) for r in rows]

    def snapshot(self) -> list[dict]:
        return [dict(r) for r in self.rows]

    def add(self, text: str):
        link = parse_link(text)  # the real parser, so bad links get the real error message
        if any(r.get("infohash") == link.infohash for r in self.rows):
            raise FetchError("already in the list")
        self.rows.append({"key": f"new{len(self.rows)}", "infohash": link.infohash,
                          "name": link.name or text.strip(), "size": None, "phase": QUEUED,
                          "state": "Queued", "progress": ""})

    def cancel(self, key: str):
        self.rows = [r for r in self.rows if r["key"] != key]

    def clear_finished(self):
        self.rows = [r for r in self.rows if r["phase"] not in FINISHED]


TABS = {"movies": 0, "series": 1, "add": 2}
VIEWS = [("movies-in-progress", 0, 0), ("movies-sent", 0, 1),
         ("series-in-progress", 1, 0), ("series-sent", 1, 1), ("add", 2, None)]


def select(window: DownloadsWindow, tab: int, inner: int | None):
    window.tabs.select(tab)
    if inner is not None:
        notebook = window.pages[tab].frame.winfo_children()[0]  # a Movies/Series tab's own tabs
        notebook.select(inner)
    window.show_status()



def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tab", choices=TABS, default="movies")
    ap.add_argument("--no-series", action="store_true", help="as if series syncing is off")
    ap.add_argument("--empty", action="store_true", help="no rows anywhere")
    args = ap.parse_args()

    sent_file = Path(tempfile.mkdtemp(prefix="stremio-sync-preview-")) / "sent.json"
    sent = {} if args.empty else {k: v for k, v in SENT.items() if not args.no_series or v.get("kind") != EPISODE}
    sent_file.write_text(json.dumps(sent, indent=2), encoding="utf-8")
    progress = [] if args.empty else [r for r in PROGRESS if not args.no_series or r["kind"] != EPISODE]

    root = tk.Tk()
    root.withdraw()
    window = DownloadsWindow(root, sent_file, status=lambda: "Watching the cache",
                             progress=lambda: progress, fetcher=FakeFetcher([] if args.empty else FETCHES),
                             series_on=not args.no_series)
    window.win.protocol("WM_DELETE_WINDOW", root.destroy)
    select(window, TABS[args.tab], None)
    window.show()
    root.mainloop()


if __name__ == "__main__":
    main()
