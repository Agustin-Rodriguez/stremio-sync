"""A Movies or Series tab, with its own In progress and Sent tabs."""

from tkinter import ttk
from typing import TYPE_CHECKING

from .common import fill_in_place, gb, progress_columns, sent_columns, table, when

if TYPE_CHECKING:
    from .window import DownloadsWindow


class MediaTab:
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
        active_tab, self.active = table(tabs, progress_columns(title), window.px)
        sent_tab, self.tree = table(tabs, sent_columns(title), window.px, on_heading=self.sort)
        tabs.add(active_tab, text="In progress")
        tabs.add(sent_tab, text="Sent")
        self.empty = ttk.Label(active_tab, text=empty_text)

    def sort(self, key: str):
        self.reverse = not self.reverse if key == self.sort_by else key in ("size", "sent", "imported")
        self.sort_by = key
        self.show_sent()

    def load_progress(self, rows: list[dict]):
        fill_in_place(self.active, rows, lambda r: (r["name"], gb(r["size"]), r["state"], r["progress"]))
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
                gb(r.get("size")),
                when(r.get("sent_at")),
                # Records from before import tracking have no size, and we can't know.
                when(r.get("imported_at")) or ("not yet" if "size" in r else ""),
            ))
