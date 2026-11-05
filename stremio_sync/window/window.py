"""The "Show downloads" window: Movies, Series and Add tabs, refreshed every 2 s."""

import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Callable

from ..cache.scan import EPISODE, MOVIE
from ..fetch import Fetcher
from ..state import SentLog
from .add_tab import AddTab
from .common import REFRESH_MS, mtime
from .media_tab import MediaTab


class DownloadsWindow:
    def __init__(self, root: tk.Tk, state_file: Path, status: Callable[[], str],
                 progress: Callable[[], list[dict]], fetcher: Fetcher, series_on: bool):
        self.state_file = state_file
        self.status = status
        self.progress = progress
        self.sent_mtime = 0.0
        # Once the process is DPI-aware tk scales fonts, but pixel sizes stay as given.
        self.scale = root.tk.call("tk", "scaling") / (96 / 72)
        ttk.Style(root).configure("Treeview", rowheight=self.px(20))
        pad = self.px(8)

        self.win = tk.Toplevel(root)
        self.win.title("stremio-sync: downloads")
        self.win.geometry(f"{self.px(880)}x{self.px(520)}")
        self.win.columnconfigure(0, weight=1)
        self.win.rowconfigure(0, weight=1)

        self.tabs = ttk.Notebook(self.win)
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=pad, pady=pad)
        series_empty = "Nothing in progress" if series_on else \
            "Series syncing is off (set [server] series_incoming)"
        self.sections = {
            MOVIE: MediaTab(self, self.tabs, "Movie", "Nothing in progress"),
            EPISODE: MediaTab(self, self.tabs, "Episode", series_empty),
        }
        self.add_tab = AddTab(self, self.tabs, fetcher)
        self.tabs.add(self.sections[MOVIE].frame, text="Movies")
        self.tabs.add(self.sections[EPISODE].frame, text="Series")
        self.tabs.add(self.add_tab.frame, text="Add")
        self.pages = [self.sections[MOVIE], self.sections[EPISODE], self.add_tab]  # in tab order
        self.tabs.bind("<<NotebookTabChanged>>", lambda _: self.show_status())

        bar = ttk.Frame(self.win)
        bar.grid(row=1, column=0, sticky="ew", padx=pad, pady=(0, pad))
        self.status_label = ttk.Label(bar)
        self.status_label.pack(side="left")
        ttk.Button(bar, text="Refresh", command=self.refresh).pack(side="right")

        self.refresh()
        self.win.after(REFRESH_MS, self.tick)

    def px(self, n: int) -> int:
        return round(n * self.scale)

    def show(self):
        self.refresh()
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

    def exists(self) -> bool:
        return bool(self.win.winfo_exists())

    def refresh(self):
        self.load_sent()
        self.load_progress()
        self.show_status()

    def tick(self):
        if not self.exists():
            return
        if mtime(self.state_file) != self.sent_mtime:
            self.load_sent()
        self.load_progress()
        self.show_status()
        self.win.after(REFRESH_MS, self.tick)

    def show_status(self):
        current = self.pages[self.tabs.index("current")]
        self.status_label.config(text=f"{current.counts}  {self.status()}")

    def load_progress(self):
        rows = self.progress()
        for kind, section in self.sections.items():
            section.load_progress([r for r in rows if r["kind"] == kind])
        self.add_tab.load()

    def load_sent(self):
        self.sent_mtime = mtime(self.state_file)
        records = SentLog(self.state_file).records()  # read fresh: the engine writes it atomically
        for kind, section in self.sections.items():
            # Records from before series support have no kind; they're all movies.
            section.load_sent([r for r in records if r.get("kind", MOVIE) == kind])
