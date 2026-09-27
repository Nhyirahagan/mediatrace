"""Provider for direct links to media files (``https://host/path/clip.mp4``)."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from email.message import Message
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from ...detection.categories import EXTENSIONS, MEDIA_CATEGORIES, MIME_TO_EXT, Category
from ...exceptions import DownloadError
from ...naming import parse_size
from ..models import DownloadedFile, DownloadOptions, MediaInfo, ProgressEvent
from ..urls import url_extension
from .base import MediaProvider

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; mediatrace/0.1; +https://pypi.org/project/mediatrace/)"
_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
_FILENAME_STAR = re.compile(r"filename\*\s*=\s*[^']*'[^']*'([^;]+)", re.IGNORECASE)
_FILENAME = re.compile(r'filename\s*=\s*"?([^";]+)"?', re.IGNORECASE)


def _filename_from_headers(headers: Message) -> str | None:
    disposition = headers.get("Content-Disposition") or ""
    m = _FILENAME_STAR.search(disposition) or _FILENAME.search(disposition)
    return unquote(m.group(1).strip()) if m else None


class DirectProvider(MediaProvider):
    """Downloads files served directly over HTTP(S). Standard library only."""

    name = "direct"

    def __init__(
        self,
        *,
        categories: frozenset[Category] | set[Category] = MEDIA_CATEGORIES,
        chunk_size: int = 1 << 16,
    ):
        self.categories = frozenset(categories)
        self.chunk_size = chunk_size

    def supports(self, url: str) -> bool:
        entry = EXTENSIONS.get(url_extension(url))
        return entry is not None and entry[1] in self.categories

    # -- HTTP helpers ----------------------------------------------------------

    def _open(self, url: str, options: DownloadOptions, *, method: str = "GET", headers: dict | None = None):
        request = Request(
            url,
            method=method,
            headers={"User-Agent": options.user_agent or DEFAULT_USER_AGENT, "Accept": "*/*", **(headers or {})},
        )
        handlers = [ProxyHandler({"http": options.proxy, "https": options.proxy})] if options.proxy else []
        return build_opener(*handlers).open(request, timeout=options.timeout)

    def _describe(self, url: str, final_url: str, headers: Message) -> MediaInfo:
        path_name = unquote(PurePosixPath(urlsplit(final_url).path).name)
        filename = _filename_from_headers(headers) or path_name or "download"
        content_type = headers.get_content_type()
        stem, _, suffix = filename.rpartition(".")
        ext = suffix.lower() if stem and suffix.lower() in EXTENSIONS else MIME_TO_EXT.get(content_type, "")
        category = EXTENSIONS[ext][1] if ext in EXTENSIONS else None
        media_type = {Category.IMAGE: "image", Category.VIDEO: "video", Category.AUDIO: "audio"}.get(
            category, "unknown"  # type: ignore[arg-type]
        )
        length = headers.get("Content-Length")
        return MediaInfo(
            id=hashlib.sha1(url.encode()).hexdigest()[:12],
            url=final_url,
            platform="direct",
            title=stem or filename,
            uploader=(urlsplit(final_url).hostname or "").removeprefix("www."),
            ext=ext,
            filesize=int(length) if length and length.isdigit() else None,
            media_type=media_type,  # type: ignore[arg-type]
            raw={"headers": dict(headers.items()), "content_type": content_type},
        )

    def _retrying(self, fn, options: DownloadOptions):
        delay = 1.0
        for attempt in range(options.retries + 1):
            try:
                return fn()
            except HTTPError as exc:
                if exc.code not in _RETRYABLE_STATUS or attempt == options.retries:
                    raise DownloadError(f"HTTP {exc.code} {exc.reason}") from exc
            except (URLError, TimeoutError, ConnectionError) as exc:
                if attempt == options.retries:
                    raise DownloadError(f"network error: {exc}") from exc
            log.debug("retrying in %.1fs (attempt %d)", delay, attempt + 1)
            time.sleep(delay)
            delay *= 2
        raise DownloadError("retries exhausted")  # pragma: no cover

    # -- provider API ------------------------------------------------------------

    def extract_info(self, url: str, options: DownloadOptions) -> MediaInfo:
        def _head() -> MediaInfo:
            try:
                with self._open(url, options, method="HEAD") as resp:
                    return self._describe(url, resp.geturl(), resp.headers)
            except HTTPError as exc:
                if exc.code not in (403, 405, 501):
                    raise
            # Some servers reject HEAD; ask for a single byte instead.
            with self._open(url, options, headers={"Range": "bytes=0-0"}) as resp:
                info = self._describe(url, resp.geturl(), resp.headers)
                content_range = resp.headers.get("Content-Range", "")
                if "/" in content_range and content_range.rsplit("/", 1)[1].isdigit():
                    info.filesize = int(content_range.rsplit("/", 1)[1])
                return info

        return self._retrying(_head, options)

    def download(
        self, url: str, staging_dir: Path, options: DownloadOptions
    ) -> tuple[MediaInfo, list[DownloadedFile]]:
        limit = parse_size(options.max_filesize)

        def _get() -> tuple[MediaInfo, Path]:
            with self._open(url, options) as resp:
                info = self._describe(url, resp.geturl(), resp.headers)
                if info.raw["content_type"] in ("text/html", "application/xhtml+xml"):
                    raise DownloadError(f"{url} returned an HTML page, not a media file")
                if limit and info.filesize and info.filesize > limit:
                    raise DownloadError(f"file is {info.filesize} bytes, over the {limit}-byte limit")
                path = staging_dir / f"{info.id}.{info.ext or 'bin'}"
                done = 0
                started = time.monotonic()
                with open(path, "wb") as fh:
                    while chunk := resp.read(self.chunk_size):
                        fh.write(chunk)
                        done += len(chunk)
                        if limit and done > limit:
                            raise DownloadError(f"download exceeded the {limit}-byte limit")
                        if options.progress:
                            elapsed = max(time.monotonic() - started, 1e-6)
                            speed = done / elapsed
                            eta = (info.filesize - done) / speed if info.filesize and speed else None
                            options.progress(
                                ProgressEvent(url, "downloading", self.name, path.name, done, info.filesize, speed, eta)
                            )
                if info.filesize and done < info.filesize:
                    raise URLError(f"connection closed early ({done}/{info.filesize} bytes)")
                info.filesize = done
            if options.progress:
                options.progress(ProgressEvent(url, "finished", self.name, path.name, done, done))
            return info, path

        info, path = self._retrying(_get, options)
        return info, [DownloadedFile(path=path, info=info, kind="media")]
