"""The "Show downloads" window: movies and series, each with what's in progress and what's been sent."""

import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Callable

from .cache import EPISODE, MOVIE
from .state import SentLog

REFRESH_MS = 2000


def sent_columns(title: str):
    return (("name", title, 460), ("size", "Size", 80), ("sent", "Sent", 150), ("imported", "Imported", 150))


def progress_columns(title: str):
    return (("name", title, 460), ("size", "Size", 80), ("state", "State", 230), ("progress", "Progress", 80))


class Section:
    """One top-level tab (Movies or Series), with its own In progress and Sent tabs."""

    def __init__(self, window: "DownloadsWindow", parent: ttk.Notebook, title: str, empty_text: str):
        self.sort_by, self.reverse = "sent", True
        self.counts = ""
        self.records: list[dict] = []
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)
        tabs = ttk.Notebook(self.frame)
        tabs.grid(row=0, column=0, sticky="nsew", padx=window.px(4), pady=window.px(4))
        active_tab, self.active = window.table(tabs, progress_columns(title))
        sent_tab, self.tree = window.table(tabs, sent_columns(title), on_heading=self.sort)
        tabs.add(active_tab, text="In progress")
        tabs.add(sent_tab, text="Sent")
        self.empty = ttk.Label(active_tab, text=empty_text)

    def sort(self, key: str):
        self.reverse = not self.reverse if key == self.sort_by else key in ("size", "sent", "imported")
        self.sort_by = key
        self.show_sent()

    def load_progress(self, rows: list[dict]):
        """Update rows in place, so the 2 s refresh keeps the selection and scroll position."""
        gone = set(self.active.get_children()) - {r["key"] for r in rows}
        if gone:
            self.active.delete(*gone)
        for i, r in enumerate(rows):
            values = (r["name"], _gb(r["size"]), r["state"], r["progress"])
            if self.active.exists(r["key"]):
                self.active.item(r["key"], values=values)
                self.active.move(r["key"], "", i)
            else:
                self.active.insert("", i, iid=r["key"], values=values)
        if rows:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.5, anchor="center")

    def load_sent(self, records: list[dict]):
        self.records = records
        imported = sum(1 for r in records if r.get("imported_at"))
        self.counts = f"{len(records)} sent, {imported} imported."
        self.show_sent()

    def show_sent(self):
        sort_key = {"name": lambda r: r.get("name", "").lower(),
                    "size": lambda r: r.get("size", 0),
                    "sent": lambda r: r.get("sent_at", ""),
                    "imported": lambda r: r.get("imported_at", "")}[self.sort_by]
        self.tree.delete(*self.tree.get_children())
        for r in sorted(self.records, key=sort_key, reverse=self.reverse):
            self.tree.insert("", "end", values=(
                r.get("name", "?"),
                _gb(r.get("size")),
                _when(r.get("sent_at")),
                # Records from before import tracking have no size, and we can't know.
                _when(r.get("imported_at")) or ("not yet" if "size" in r else ""),
            ))


class DownloadsWindow:
    def __init__(self, root: tk.Tk, state_file: Path, status: Callable[[], str],
                 progress: Callable[[], list[dict]], series_on: bool):
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
            MOVIE: Section(self, self.tabs, "Movie", "Nothing in progress"),
            EPISODE: Section(self, self.tabs, "Episode", series_empty),
        }
        self.tabs.add(self.sections[MOVIE].frame, text="Movies")
        self.tabs.add(self.sections[EPISODE].frame, text="Series")
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

    def table(self, parent: tk.Widget, columns, on_heading=None) -> tuple[ttk.Frame, ttk.Treeview]:
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings")
        for key, title, width in columns:
            tree.heading(key, text=title)
            if on_heading:
                tree.heading(key, command=lambda k=key: on_heading(k))
            tree.column(key, width=self.px(width), anchor="e" if key in ("size", "progress") else "w",
                        stretch=key == "name")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        return frame, tree

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
        if _mtime(self.state_file) != self.sent_mtime:
            self.load_sent()
        self.load_progress()
        self.show_status()
        self.win.after(REFRESH_MS, self.tick)

    def show_status(self):
        current = self.sections[EPISODE if self.tabs.index("current") == 1 else MOVIE]
        self.status_label.config(text=f"{current.counts}  {self.status()}")

    def load_progress(self):
        rows = self.progress()
        for kind, section in self.sections.items():
            section.load_progress([r for r in rows if r["kind"] == kind])

    def load_sent(self):
        self.sent_mtime = _mtime(self.state_file)
        records = SentLog(self.state_file).records()  # read fresh: the engine writes it atomically
        for kind, section in self.sections.items():
            # Records from before series support have no kind; they're all movies.
            section.load_sent([r for r in records if r.get("kind", MOVIE) == kind])


def _gb(size: int | None) -> str:
    return f"{size / 1e9:.2f} GB" if size else ""


def _when(iso: str | None) -> str:
    return iso.replace("T", " ")[:16] if iso else ""


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
