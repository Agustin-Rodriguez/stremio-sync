"""A download's saved states, and how the Add tab shows them."""

# What the Add tab shows is worked out from these plus what's happening now.
QUEUED, INFO, DOWNLOADING, DONE, FAILED = "queued", "info", "downloading", "done", "failed"
FINISHED = (DONE, FAILED)
SAVED = ("id", "link", "infohash", "index", "name", "size", "kind", "files", "done_files",
         "state", "error", "added_at")


def shown(rec: dict, held: bool) -> dict:
    """The State and Progress columns."""
    state = rec["state"]
    if state == QUEUED:
        return {"state": "Queued", "progress": ""}
    if state == DONE:
        return {"state": "Done", "progress": "100%"}
    if state == FAILED:
        return {"state": f"Failed: {rec['error']}", "progress": ""}
    progress = ""
    if rec.get("done_bytes") is not None and rec.get("size"):
        progress = f"{rec['done_bytes'] * 100 // rec['size']}%"  # rounded down, so 99.7% doesn't read as done
        if rec.get("speed"):
            progress += f"  {rec['speed'] / 1e6:.1f} MB/s"
    if held:
        text = "Paused"
    elif rec.get("down"):
        text = "Waiting for Stremio to open"
    elif state == INFO:
        text = "Getting torrent info"
    elif rec.get("stalled"):
        text = "Stalled, no peers sending"
    else:
        n, total = rec.get("part") or (1, 1)
        text = f"Downloading episode {n} of {total}" if total > 1 else "Downloading"
    if rec.get("note") and not rec.get("down"):
        text += f" (retrying: {rec['note']})"
    return {"state": text, "progress": progress}
