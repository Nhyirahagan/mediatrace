"""Provider-neutral data models for media metadata, options and results."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

MediaType = Literal["video", "audio", "image", "collection", "unknown"]
FileKind = Literal["media", "thumbnail", "subtitle", "metadata"]


@dataclass(slots=True)
class MediaFormat:
    format_id: str
    ext: str = ""
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    vcodec: str | None = None
    acodec: str | None = None
    filesize: int | None = None
    bitrate: float | None = None
    note: str = ""

    @property
    def has_video(self) -> bool:
        return bool(self.vcodec)

    @property
    def has_audio(self) -> bool:
        return bool(self.acodec)

    @property
    def resolution(self) -> str:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        if self.height:
            return f"{self.height}p"
        return "audio only" if self.has_audio and not self.has_video else ""


@dataclass(slots=True)
class MediaInfo:
    """Normalized metadata for a post/video/track, or a collection of them."""

    id: str
    url: str
    platform: str
    title: str = ""
    uploader: str = ""
    uploader_id: str = ""
    description: str = ""
    duration: float | None = None
    upload_date: date | None = None
    thumbnail: str | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    tags: list[str] = field(default_factory=list)
    width: int | None = None
    height: int | None = None
    ext: str = ""
    filesize: int | None = None
    media_type: MediaType = "unknown"
    formats: list[MediaFormat] = field(default_factory=list)
    entries: list[MediaInfo] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_collection(self) -> bool:
        return self.media_type == "collection" or bool(self.entries)

    def walk(self):
        """Yield this item and all nested entries."""
        yield self
        for entry in self.entries:
            yield from entry.walk()

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "url": self.url,
            "platform": self.platform,
            "title": self.title,
            "uploader": self.uploader,
            "uploader_id": self.uploader_id,
            "description": self.description,
            "duration": self.duration,
            "upload_date": self.upload_date.isoformat() if self.upload_date else None,
            "thumbnail": self.thumbnail,
            "view_count": self.view_count,
            "like_count": self.like_count,
            "comment_count": self.comment_count,
            "tags": list(self.tags),
            "width": self.width,
            "height": self.height,
            "ext": self.ext,
            "filesize": self.filesize,
            "media_type": self.media_type,
            "formats": [
                {
                    "format_id": f.format_id,
                    "ext": f.ext,
                    "resolution": f.resolution,
                    "fps": f.fps,
                    "vcodec": f.vcodec,
                    "acodec": f.acodec,
                    "filesize": f.filesize,
                    "bitrate": f.bitrate,
                    "note": f.note,
                }
                for f in self.formats
            ],
            "entries": [e.to_dict(include_raw=include_raw) for e in self.entries],
        }
        if include_raw:
            data["raw"] = self.raw
        return data


@dataclass(slots=True)
class ProgressEvent:
    url: str
    status: str  # "downloading" | "finished" | "error"
    provider: str
    filename: str | None = None
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    speed: float | None = None
    eta: float | None = None

    @property
    def fraction(self) -> float | None:
        if self.downloaded_bytes is not None and self.total_bytes:
            return min(1.0, self.downloaded_bytes / self.total_bytes)
        return None


_QUALITY_ALIASES = {"4k": 2160, "uhd": 2160, "2k": 1440, "qhd": 1440, "fhd": 1080, "fullhd": 1080, "hd": 720, "sd": 480}


@dataclass(slots=True)
class DownloadOptions:
    """How to fetch media. Every field can be overridden per call on :class:`MediaClient`."""

    quality: str | int = "best"  # "best", "worst", "audio", "1080p", 720, "4k"
    audio_only: bool = False
    audio_format: str = "mp3"  # used when extracting audio (needs ffmpeg)
    video_format: str | None = "mp4"  # container to merge/remux into (needs ffmpeg)
    format: str | None = None  # raw provider-specific selector (yt-dlp format string)
    playlist: bool = False  # expand playlists / whole channels when a URL is ambiguous
    playlist_items: str | None = None  # e.g. "1-5,8"
    subtitles: bool = False
    subtitle_langs: tuple[str, ...] = ("en",)
    thumbnail: bool = False
    write_metadata: bool = False  # write a normalized <name>.info.json next to the media
    cookies_file: str | Path | None = None  # Netscape cookies.txt for content you have access to
    proxy: str | None = None
    rate_limit: str | int | None = None  # bytes/s, e.g. "2M"
    max_filesize: str | int | None = None  # e.g. "500MB"
    timeout: float = 30.0
    retries: int = 3
    user_agent: str | None = None
    progress: Callable[[ProgressEvent], None] | None = None

    @property
    def wants_audio_only(self) -> bool:
        return self.audio_only or str(self.quality).lower() == "audio"

    @property
    def max_height(self) -> int | None:
        q = self.quality
        if isinstance(q, int):
            return q
        text = str(q).strip().lower()
        if text in _QUALITY_ALIASES:
            return _QUALITY_ALIASES[text]
        m = re.fullmatch(r"(\d{3,4})p?", text)
        return int(m.group(1)) if m else None


@dataclass(slots=True)
class DownloadedFile:
    path: Path
    info: MediaInfo
    kind: FileKind = "media"
    label: str = ""  # sidecar label, e.g. "en" for subtitles, "thumb" for thumbnails
    steps: list[str] = field(default_factory=list)  # cleaning steps applied
    skipped: bool = False  # True when an existing file was kept (conflict="skip")


@dataclass(slots=True)
class DownloadResult:
    url: str
    normalized_url: str
    provider: str
    info: MediaInfo
    files: list[DownloadedFile] = field(default_factory=list)

    @property
    def media_files(self) -> list[Path]:
        return [f.path for f in self.files if f.kind == "media"]

    @property
    def primary(self) -> Path | None:
        media = self.media_files
        return media[0] if media else None


@dataclass(slots=True)
class BatchResult:
    results: list[DownloadResult] = field(default_factory=list)
    errors: dict[str, Exception] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors
