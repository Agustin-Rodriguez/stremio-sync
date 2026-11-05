"""The Add tab: paste a link and Stremio downloads it."""

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

from ..fetch import FINISHED, QUEUED, FetchError, Fetcher
from .common import fill_in_place, gb, table

if TYPE_CHECKING:
    from .window import DownloadsWindow

ADD_HINT = "Paste a Stremio stream link or a magnet. Stremio has to be open while it downloads."
ADD_COLUMNS = (("name", "Name", 400), ("size", "Size", 80), ("state", "State", 250), ("progress", "Progress", 130))


class AddTab:
    """Paste a link to have Stremio download it; lists what was pasted and how it's going."""

    def __init__(self, window: "DownloadsWindow", parent: ttk.Notebook, fetcher: Fetcher):
        self.fetcher = fetcher
        self.counts = ""
        pad = window.px(4)
        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(2, weight=1)

        bar = ttk.Frame(self.frame)
        bar.grid(row=0, column=0, sticky="ew", padx=pad, pady=(pad, 0))
        bar.columnconfigure(0, weight=1)
        self.link = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.link)
        entry.grid(row=0, column=0, sticky="ew")
        entry.bind("<Return>", lambda _: self.add())
        ttk.Button(bar, text="Download", command=self.add).grid(row=0, column=1, padx=(pad, 0))
        self.message = ttk.Label(self.frame, text=ADD_HINT)
        self.message.grid(row=1, column=0, sticky="w", padx=pad, pady=pad)

        list_frame, self.tree = table(self.frame, ADD_COLUMNS, window.px)
        list_frame.grid(row=2, column=0, sticky="nsew", padx=pad)
        self.empty = ttk.Label(list_frame, text="Nothing added")

        buttons = ttk.Frame(self.frame)
        buttons.grid(row=3, column=0, sticky="ew", padx=pad, pady=pad)
        ttk.Button(buttons, text="Clear finished", command=self.clear).pack(side="right")
        ttk.Button(buttons, text="Cancel", command=self.cancel).pack(side="right", padx=(0, pad))

    def add(self):
        try:
            self.fetcher.add(self.link.get())
        except FetchError as e:
            self.message.config(text=f"Can't add that: {e}.")
            return
        self.link.set("")
        self.message.config(text=ADD_HINT)
        self.load()

    def cancel(self):
        for key in self.tree.selection():
            self.fetcher.cancel(key)
        self.load()

    def clear(self):
        self.fetcher.clear_finished()
        self.load()

    def load(self):
        rows = self.fetcher.snapshot()
        fill_in_place(self.tree, rows, lambda r: (r["name"], gb(r["size"]), r["state"], r["progress"]))
        queued = sum(1 for r in rows if r["phase"] == QUEUED)
        active = sum(1 for r in rows if r["phase"] not in FINISHED) - queued
        self.counts = f"{active} downloading, {queued} queued."
        if rows:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.5, anchor="center")
