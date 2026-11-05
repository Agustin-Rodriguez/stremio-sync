"""What the window's tabs share: table building, in-place refresh and cell formatting."""

import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Callable

REFRESH_MS = 2000


def sent_columns(title: str):
    return (("name", title, 460), ("size", "Size", 80), ("sent", "Sent", 150), ("imported", "Imported", 150))


def progress_columns(title: str):
    return (("name", title, 460), ("size", "Size", 80), ("state", "State", 230), ("progress", "Progress", 80))


def fill_in_place(tree: ttk.Treeview, rows: list[dict], values: Callable[[dict], tuple]):
    """Update rows in place, so the 2 s refresh keeps the selection and scroll position."""
    gone = set(tree.get_children()) - {r["key"] for r in rows}
    if gone:
        tree.delete(*gone)
    for i, r in enumerate(rows):
        if tree.exists(r["key"]):
            tree.item(r["key"], values=values(r))
            tree.move(r["key"], "", i)
        else:
            tree.insert("", i, iid=r["key"], values=values(r))


def table(parent: tk.Widget, columns, px: Callable[[int], int], on_heading=None) -> tuple[ttk.Frame, ttk.Treeview]:
    frame = ttk.Frame(parent)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings")
    for key, title, width in columns:
        tree.heading(key, text=title)
        if on_heading:
            tree.heading(key, command=lambda k=key: on_heading(k))
        tree.column(key, width=px(width), anchor="e" if key in ("size", "progress") else "w",
                    stretch=key == "name")
    scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.grid(row=0, column=0, sticky="nsew")
    scroll.grid(row=0, column=1, sticky="ns")
    return frame, tree


def gb(size: int | None) -> str:
    return f"{size / 1e9:.2f} GB" if size else ""


def when(iso: str | None) -> str:
    return iso.replace("T", " ")[:16] if iso else ""


def mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
