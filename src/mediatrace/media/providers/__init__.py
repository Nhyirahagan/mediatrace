"""Media providers."""

from .base import MediaProvider
from .direct import DirectProvider
from .ytdlp import YtDlpProvider, info_from_ytdlp


def default_providers() -> list[MediaProvider]:
    """Direct file links first (cheap, exact), then yt-dlp for platform pages."""
    return [DirectProvider(), YtDlpProvider()]


__all__ = ["DirectProvider", "MediaProvider", "YtDlpProvider", "default_providers", "info_from_ytdlp"]
