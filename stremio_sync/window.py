"""The "Show movies" window: what's been sent, and whether Radarr imported it."""

import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Callable

from .state import SentLog

COLUMNS = (("name", "Movie", 460), ("size", "Size", 80), ("sent", "Sent", 150), ("imported", "Imported", 150))


class MoviesWindow:
    def __init__(self, root: tk.Tk, state_file: Path, status: Callable[[], str]):
        self.state_file = state_file
        self.status = status
        self.sort_by, self.reverse = "sent", True

        self.win = tk.Toplevel(root)
        self.win.title("stremio-sync: sent movies")
        self.win.geometry("880x480")
        self.win.columnconfigure(0, weight=1)
        self.win.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(self.win, columns=[c[0] for c in COLUMNS], show="headings")
        for key, title, width in COLUMNS:
            self.tree.heading(key, text=title, command=lambda k=key: self.sort(k))
            self.tree.column(key, width=width, anchor="e" if key == "size" else "w", stretch=key == "name")
        scroll = ttk.Scrollbar(self.win, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        scroll.grid(row=0, column=1, sticky="ns", pady=8, padx=(0, 8))

        bar = ttk.Frame(self.win)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))
        self.status_label = ttk.Label(bar)
        self.status_label.pack(side="left")
        ttk.Button(bar, text="Refresh", command=self.refresh).pack(side="right")

        self.refresh()

    def show(self):
        self.refresh()
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

    def exists(self) -> bool:
        return bool(self.win.winfo_exists())

    def sort(self, key: str):
        self.reverse = not self.reverse if key == self.sort_by else key in ("size", "sent", "imported")
        self.sort_by = key
        self.refresh()

    def refresh(self):
        records = SentLog(self.state_file).records()  # read fresh: the engine writes it atomically
        sort_key = {"name": lambda r: r.get("name", "").lower(),
                    "size": lambda r: r.get("size", 0),
                    "sent": lambda r: r.get("sent_at", ""),
                    "imported": lambda r: r.get("imported_at", "")}[self.sort_by]
        self.tree.delete(*self.tree.get_children())
        for r in sorted(records, key=sort_key, reverse=self.reverse):
            self.tree.insert("", "end", values=(
                r.get("name", "?"),
                f"{r['size'] / 1e9:.2f} GB" if r.get("size") else "",
                _when(r.get("sent_at")),
                # Records from before import tracking have no size, and we can't know.
                _when(r.get("imported_at")) or ("not yet" if "size" in r else ""),
            ))
        imported = sum(1 for r in records if r.get("imported_at"))
        self.status_label.config(text=f"{len(records)} sent, {imported} imported.  {self.status()}")


def _when(iso: str | None) -> str:
    return iso.replace("T", " ")[:16] if iso else ""
