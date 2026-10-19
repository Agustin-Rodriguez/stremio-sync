"""The tray icon and its menu. tkinter owns the main thread; the icon and the engine run on their own."""

import ctypes
import logging
import os
import queue
import threading
import tkinter as tk
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

from . import autostart
from .config import PROJECT_DIR, Config
from .engine import BUSY, ERROR, PAUSE_SENDING, RUNNING, SENDING, STOPPED, Engine
from .window import MoviesWindow

log = logging.getLogger(__name__)

COLORS = {
    RUNNING: "#2e9e4f",
    BUSY: "#2e9e4f",
    SENDING: "#2f6fd6",
    PAUSE_SENDING: "#e0a020",
    STOPPED: "#8a8a8a",
    ERROR: "#d23b3b",
}
_mutex = None  # held for the life of the process


def icon_image(color: str) -> Image.Image:
    # Drawn large so Windows downscales a sharp source at any DPI.
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((16, 16, 240, 240), fill=color, outline="white", width=16)
    draw.polygon([(104, 80), (104, 176), (184, 128)], fill="white")  # a play triangle
    return img


def dpi_aware():
    """Draw at the screen's real resolution instead of letting Windows stretch a blurry bitmap."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system-aware: Tk 8.6 can't rescale per monitor
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()


def single_instance() -> bool:
    """False if another tray app is already running."""
    global _mutex
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _mutex = kernel32.CreateMutexW(None, False, "Local\\stremio-sync-tray")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def main(cfg: Config, config_path: Path, log_file: Path):
    if not single_instance():
        log.info("Already running, see the tray.")
        return

    dpi_aware()  # before tk or pystray create any window
    root = tk.Tk()
    root.withdraw()
    tasks: queue.Queue = queue.Queue()  # callables to run on the tk thread
    window: MoviesWindow | None = None
    images = {state: icon_image(color) for state, color in COLORS.items()}

    def on_status(state: str, text: str):
        icon.icon = images[state]
        icon.title = f"stremio-sync: {text}"[:127]  # Windows tooltip limit
        icon.update_menu()  # the Windows menu is built ahead of time, so checkmarks need a refresh

    engine = Engine(cfg, on_status)

    def show_movies():
        nonlocal window
        if window is None or not window.exists():
            window = MoviesWindow(root, cfg.state_file, lambda: engine.status[1], engine.progress)
        window.show()

    def toggle_autostart():
        try:
            if autostart.is_enabled():
                autostart.disable()
            else:
                autostart.enable(PROJECT_DIR / "sync.py", config_path.resolve())
        except OSError as e:
            log.error("Can't change Start at login: %s", e)
        icon.update_menu()

    def stop_then_quit():
        engine.stop()
        tasks.put(root.quit)

    def quit_app():
        icon.title = "stremio-sync: quitting after the current step"
        threading.Thread(target=stop_then_quit, daemon=True).start()

    def set_mode(mode: str):
        engine.set_mode(mode)
        icon.update_menu()

    def mode_item(label: str, mode: str):
        return pystray.MenuItem(label, lambda: set_mode(mode),
                                checked=lambda _: engine.mode == mode, radio=True)

    icon = pystray.Icon("stremio-sync", images[RUNNING], "stremio-sync", menu=pystray.Menu(
        mode_item("Running", RUNNING),
        mode_item("Pause sending", PAUSE_SENDING),
        mode_item("Stop watching", STOPPED),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Sync now", lambda: engine.sync_now()),
        pystray.MenuItem("Show movies…", lambda: tasks.put(show_movies), default=True),
        pystray.MenuItem("Open log", lambda: os.startfile(log_file)),
        pystray.MenuItem("Start at login", toggle_autostart, checked=lambda _: autostart.is_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_app),
    ))

    def drain():
        while not tasks.empty():
            tasks.get_nowait()()
        root.after(200, drain)

    log.info("\nTray app started, watching %s", cfg.cache_dir)
    icon.run_detached()
    engine.start()
    root.after(200, drain)
    root.mainloop()
    icon.stop()
    log.info("\nTray app stopped.")
