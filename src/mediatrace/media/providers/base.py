"""Provider interface: plug any media source into :class:`~mediatrace.media.MediaClient`."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from ..models import DownloadedFile, DownloadOptions, MediaInfo


class MediaProvider(ABC):
    """A source of media.

    Providers download into a private *staging* directory the client gives them;
    the client then detects real file types, cleans the files and moves them to
    their final, template-based names. That keeps providers small: they only
    have to fetch bytes and describe what they fetched.

    To add a platform, subclass this and register it::

        class MyProvider(MediaProvider):
            name = "myplatform"
            def supports(self, url): return "myplatform.example" in url
            def extract_info(self, url, options): ...
            def download(self, url, staging_dir, options): ...

        client.register_provider(MyProvider())
    """

    name: ClassVar[str] = "base"

    def is_available(self) -> bool:
        """False when an optional dependency is missing."""
        return True

    @abstractmethod
    def supports(self, url: str) -> bool:
        """Whether this provider can handle ``url`` (should be cheap and offline)."""

    @abstractmethod
    def extract_info(self, url: str, options: DownloadOptions) -> MediaInfo:
        """Fetch metadata without downloading media."""

    @abstractmethod
    def download(
        self, url: str, staging_dir: Path, options: DownloadOptions
    ) -> tuple[MediaInfo, list[DownloadedFile]]:
        """Download into ``staging_dir`` and describe every file produced."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r} available={self.is_available()}>"
