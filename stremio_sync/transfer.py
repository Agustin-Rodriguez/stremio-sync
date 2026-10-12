"""Copying files to the server with ssh/scp (Windows' built-in OpenSSH works)."""

import os
import shlex
import subprocess
import sys

from .cache import CacheEntry

# BatchMode: fail instead of prompting for a password (important when nobody's watching).
# ServerAlive*: if the server stops answering for ~60s, give up instead of hanging forever.
SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=4",
]

# Under pythonw.exe (e.g. Task Scheduler) there's no console, so Windows would pop up a
# console window for every ssh/scp call. CREATE_NO_WINDOW stops that. When run from a
# terminal it stays off, so scp can still draw its progress bar.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" and sys.stdout is None else 0


class TransferError(Exception):
    pass


def ssh(target: str, command: str):
    # stdin=DEVNULL: on Windows, ssh attached to console input can hang after the command finishes.
    return subprocess.run(
        ["ssh", *SSH_OPTS, target, command],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, creationflags=NO_WINDOW,
    )


def send(entry: CacheEntry, target: str, dest: str) -> str:
    """Copy one file to the server. Returns 'sent' or 'exists'; raises TransferError."""
    # Temp name has no spaces (sidesteps scp quoting) and isn't a video extension (Radarr ignores it).
    tmp = f"{dest}/.incoming-{entry.infohash[:16]}-{entry.index}.partial"
    final = f"{dest}/{entry.name}"
    q_dest, q_tmp, q_final = shlex.quote(dest), shlex.quote(tmp), shlex.quote(final)

    r = ssh(target, f"mkdir -p {q_dest} && if [ -e {q_final} ]; then echo exists; fi")
    if r.returncode != 0:
        raise TransferError(f"ssh failed: {r.stderr.strip() or 'exit ' + str(r.returncode)}")
    if r.stdout.strip() == "exists":
        return "exists"

    # Run from inside the cache folder with the bare file name ("0", "1", ...), so scp never
    # sees a Windows drive letter like C: in the source path. stdout stays on the terminal so
    # scp draws its progress bar; scp hides it by itself when there's no terminal.
    r = subprocess.run(
        ["scp", *SSH_OPTS, entry.src.name, f"{target}:{tmp}"],
        cwd=entry.src.parent, stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        creationflags=NO_WINDOW,
    )
    if r.returncode != 0:
        ssh(target, f"rm -f {q_tmp}")
        raise TransferError(f"scp failed: {r.stderr.strip() or 'exit ' + str(r.returncode)}")

    r = ssh(target, f"if [ -e {q_final} ]; then rm -f {q_tmp}; exit 3; fi; mv -- {q_tmp} {q_final}")
    if r.returncode == 3:
        return "exists"
    if r.returncode != 0:
        raise TransferError(f"rename on server failed: {r.stderr.strip()}")
    return "sent"
