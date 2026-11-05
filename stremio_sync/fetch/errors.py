"""Why a pasted link can't be downloaded."""


class FetchError(Exception):
    pass


class Unsendable(FetchError):
    """The torrent has nothing stremio-sync would send, so downloading it is pointless."""
