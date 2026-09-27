"""yt-dlp backed provider: YouTube, TikTok, Instagram, X/Twitter, Reddit, Vimeo, SoundCloud and ~1800 more."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ...detection.categories import EXTENSIONS, Category
from ...exceptions import DownloadError, ProviderUnavailableError
from ...imaging import ffmpeg_path
from ...naming import parse_size
from ..models import DownloadedFile, DownloadOptions, MediaFormat, MediaInfo, ProgressEvent
from ..urls import PLATFORM_DOMAINS, detect_platform
from .base import MediaProvider

log = logging.getLogger(__name__)

_SUBTITLE_EXTS = frozenset({"vtt", "srt", "ass", "ssa", "ttml", "srv1", "srv2", "srv3", "json3", "lrc", "sbv"})
_PARTIAL_SUFFIXES = (".part", ".ytdl", ".temp", ".tmp")


class _Logger:
    """Route yt-dlp's console output into :mod:`logging`."""

    def debug(self, msg: str) -> None:
        log.debug(msg)

    def info(self, msg: str) -> None:
        log.debug(msg)

    def warning(self, msg: str) -> None:
        log.warning(msg)

    def error(self, msg: str) -> None:
        log.error(msg)


def _codec(value: Any) -> str | None:
    return None if value in (None, "none", "") else str(value)


def _upload_date(d: dict[str, Any]) -> date | None:
    raw = d.get("upload_date") or d.get("release_date")
    if raw and len(str(raw)) == 8:
        try:
            return datetime.strptime(str(raw), "%Y%m%d").date()
        except ValueError:
            pass
    ts = d.get("timestamp") or d.get("release_timestamp")
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=UTC).date()
    return None


def info_from_ytdlp(d: dict[str, Any], fallback_url: str = "") -> MediaInfo:
    """Convert a (sanitized) yt-dlp info dict into a :class:`MediaInfo`."""
    url = d.get("webpage_url") or d.get("original_url") or d.get("url") or fallback_url
    entries = [info_from_ytdlp(e, url) for e in (d.get("entries") or []) if isinstance(e, dict)]
    formats = [
        MediaFormat(
            format_id=str(f.get("format_id") or ""),
            ext=f.get("ext") or "",
            width=f.get("width"),
            height=f.get("height"),
            fps=f.get("fps"),
            vcodec=_codec(f.get("vcodec")),
            acodec=_codec(f.get("acodec")),
            filesize=f.get("filesize") or f.get("filesize_approx"),
            bitrate=f.get("tbr"),
            note=f.get("format_note") or "",
        )
        for f in d.get("formats") or []
        if isinstance(f, dict)
    ]
    ext = (d.get("ext") or "").lower()
    if d.get("_type") in ("playlist", "multi_video") or entries:
        media_type = "collection"
    elif ext in EXTENSIONS and EXTENSIONS[ext][1] is Category.IMAGE:
        media_type = "image"
    elif d.get("vcodec") == "none" and _codec(d.get("acodec")):
        media_type = "audio"
    elif ext in EXTENSIONS and EXTENSIONS[ext][1] is Category.AUDIO:
        media_type = "audio"
    else:
        media_type = "video"
    platform = detect_platform(url) if url else "generic"
    if platform in ("generic", "direct"):
        platform = str(d.get("extractor_key") or d.get("ie_key") or "generic").lower()
    return MediaInfo(
        id=str(d.get("id") or ""),
        url=url,
        platform=platform,
        title=d.get("title") or d.get("fulltitle") or "",
        uploader=d.get("uploader") or d.get("channel") or d.get("creator") or d.get("uploader_id") or "",
        uploader_id=str(d.get("uploader_id") or d.get("channel_id") or ""),
        description=d.get("description") or "",
        duration=d.get("duration"),
        upload_date=_upload_date(d),
        thumbnail=d.get("thumbnail"),
        view_count=d.get("view_count"),
        like_count=d.get("like_count"),
        comment_count=d.get("comment_count"),
        tags=[str(t) for t in d.get("tags") or []],
        width=d.get("width"),
        height=d.get("height"),
        ext=ext,
        filesize=d.get("filesize") or d.get("filesize_approx"),
        media_type=media_type,  # type: ignore[arg-type]
        formats=formats,
        entries=entries,
        raw=d,
    )


def _iter_dicts(d: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield d
    for entry in d.get("entries") or []:
        if isinstance(entry, dict):
            yield from _iter_dicts(entry)


class YtDlpProvider(MediaProvider):
    """Wraps `yt-dlp <https://github.com/yt-dlp/yt-dlp>`_ (``pip install mediatrace[media]``).

    Args:
        extra_options: Raw yt-dlp options merged over the generated ones.
        allow_generic: Also claim URLs only yt-dlp's generic extractor would
            handle (arbitrary web pages with embedded media).
    """

    name = "yt-dlp"
    _extractors: list[Any] | None = None

    def __init__(self, *, extra_options: dict[str, Any] | None = None, allow_generic: bool = False):
        self.extra_options = dict(extra_options or {})
        self.allow_generic = allow_generic

    def is_available(self) -> bool:
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            return False
        return True

    def _ytdlp(self):
        try:
            import yt_dlp
        except ImportError as exc:
            raise ProviderUnavailableError(
                "yt-dlp is not installed; install it with: pip install 'mediatrace[media]'"
            ) from exc
        return yt_dlp

    def supports(self, url: str) -> bool:
        if not self.is_available():
            return False
        if detect_platform(url) in PLATFORM_DOMAINS:
            return True
        if YtDlpProvider._extractors is None:
            from yt_dlp.extractor import gen_extractor_classes

            YtDlpProvider._extractors = [ie for ie in gen_extractor_classes() if ie.ie_key() != "Generic"]
        for ie in YtDlpProvider._extractors:
            try:
                if ie.suitable(url):
                    return True
            except Exception:  # a broken extractor regex must not break resolution
                continue
        return self.allow_generic

    # -- option building ------------------------------------------------------

    def _base_options(self, options: DownloadOptions) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": False,
            "noprogress": True,
            "logger": _Logger(),
            "noplaylist": not options.playlist,
            "socket_timeout": options.timeout,
            "retries": options.retries,
            "fragment_retries": options.retries,
        }
        if options.cookies_file:
            opts["cookiefile"] = str(Path(options.cookies_file).expanduser())
        if options.proxy:
            opts["proxy"] = options.proxy
        if options.rate_limit:
            opts["ratelimit"] = parse_size(options.rate_limit)
        if options.max_filesize:
            opts["max_filesize"] = parse_size(options.max_filesize)
        if options.user_agent:
            opts["http_headers"] = {"User-Agent": options.user_agent}
        if options.playlist_items:
            opts["playlist_items"] = options.playlist_items
        if ffmpeg := ffmpeg_path():
            opts["ffmpeg_location"] = ffmpeg
        return opts

    @staticmethod
    def format_selector(options: DownloadOptions, can_merge: bool) -> str:
        if options.format:
            return options.format
        if options.wants_audio_only:
            return "bestaudio/best"
        height = options.max_height
        cap = f"[height<={height}]" if height else ""
        if str(options.quality).lower() == "worst":
            return "worstvideo+worstaudio/worst" if can_merge else "worst"
        if can_merge:
            return f"bestvideo*{cap}+bestaudio/best{cap}/best"
        # Without ffmpeg only pre-muxed formats can be used.
        return f"best{cap}/best"

    # -- provider API ----------------------------------------------------------

    def extract_info(self, url: str, options: DownloadOptions) -> MediaInfo:
        yt_dlp = self._ytdlp()
        opts = self._base_options(options) | {"skip_download": True, "extract_flat": "in_playlist"}
        opts.update(self.extra_options)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                raw = ydl.extract_info(url, download=False)
                data = ydl.sanitize_info(raw)
        except yt_dlp.utils.YoutubeDLError as exc:
            raise DownloadError(_clean_error(exc)) from exc
        if not data:
            raise DownloadError(f"no information returned for {url}")
        return info_from_ytdlp(data, url)

    def download(
        self, url: str, staging_dir: Path, options: DownloadOptions
    ) -> tuple[MediaInfo, list[DownloadedFile]]:
        yt_dlp = self._ytdlp()
        can_merge = ffmpeg_path() is not None
        opts = self._base_options(options)
        opts.update(
            {
                "paths": {"home": str(staging_dir), "temp": str(staging_dir)},
                "outtmpl": {"default": "%(id)s.%(ext)s", "thumbnail": "%(id)s.thumbnail.%(ext)s"},
                "windowsfilenames": True,
                "overwrites": True,
                "format": self.format_selector(options, can_merge),
                "writethumbnail": options.thumbnail,
                "writesubtitles": options.subtitles,
                "writeautomaticsub": False,
                "subtitleslangs": list(options.subtitle_langs),
                "ignoreerrors": "only_download" if options.playlist else False,
            }
        )
        postprocessors: list[dict[str, Any]] = []
        if options.wants_audio_only and can_merge and options.audio_format:
            postprocessors.append(
                {"key": "FFmpegExtractAudio", "preferredcodec": options.audio_format, "preferredquality": "0"}
            )
        elif can_merge and options.video_format and not options.wants_audio_only:
            opts["merge_output_format"] = options.video_format
        if postprocessors:
            opts["postprocessors"] = postprocessors
        if options.progress:
            opts["progress_hooks"] = [self._progress_adapter(url, options)]
        opts.update(self.extra_options)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                raw = ydl.extract_info(url, download=True)
                if not raw:
                    raise DownloadError(f"nothing was downloaded from {url}")
                # Map on-disk stems (sanitized ids) back to the ids they came from.
                stems: dict[str, str] = {}
                for entry in _iter_dicts(raw):
                    if entry.get("id") is None:
                        continue
                    try:
                        filename = Path(ydl.prepare_filename(entry)).name
                    except Exception:
                        continue
                    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
                    stems[stem] = str(entry["id"])
                data = ydl.sanitize_info(raw)
        except yt_dlp.utils.YoutubeDLError as exc:
            raise DownloadError(_clean_error(exc)) from exc

        info = info_from_ytdlp(data, url)
        files = self._collect(staging_dir, info, stems)
        if not any(f.kind == "media" for f in files):
            raise DownloadError(f"yt-dlp finished but produced no media files for {url}")
        return info, files

    def _progress_adapter(self, url: str, options: DownloadOptions):
        def hook(d: dict[str, Any]) -> None:
            assert options.progress is not None
            options.progress(
                ProgressEvent(
                    url=url,
                    status=str(d.get("status")),
                    provider=self.name,
                    filename=Path(d["filename"]).name if d.get("filename") else None,
                    downloaded_bytes=d.get("downloaded_bytes"),
                    total_bytes=d.get("total_bytes") or d.get("total_bytes_estimate"),
                    speed=d.get("speed"),
                    eta=d.get("eta"),
                )
            )

        return hook

    @staticmethod
    def _collect(staging_dir: Path, info: MediaInfo, stems: dict[str, str]) -> list[DownloadedFile]:
        by_id = {item.id: item for item in info.walk() if item.id}
        files: list[DownloadedFile] = []
        ordered_stems = sorted(stems, key=len, reverse=True)
        for path in sorted(staging_dir.rglob("*")):
            if not path.is_file() or path.name.endswith(_PARTIAL_SUFFIXES):
                continue
            entry, rest = info, path.name
            for stem in ordered_stems:
                if path.name.startswith(stem + "."):
                    entry = by_id.get(stems[stem], info)
                    rest = path.name[len(stem) + 1 :]
                    break
            parts = rest.split(".")
            ext = parts[-1].lower()
            middle = ".".join(parts[:-1]) if len(parts) > 1 else ""
            if middle == "thumbnail":
                kind, label = "thumbnail", "thumb"
            elif ext in _SUBTITLE_EXTS:
                kind, label = "subtitle", middle or "sub"
            else:
                kind, label = "media", middle
            files.append(DownloadedFile(path=path, info=entry, kind=kind, label=label))  # type: ignore[arg-type]
        return files


def _clean_error(exc: Exception) -> str:
    message = str(exc)
    return message.removeprefix("ERROR: ").strip() or type(exc).__name__
