"""Unified API for fetching and cleaning media from public platforms."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import shutil
import tempfile
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

from ..detection import FileTypeDetector
from ..exceptions import DownloadError, MediaError, UnsupportedURLError
from ..naming import path_key, render_template, split_name, unique_path, validate_template
from .cleaning import CleanOptions, MediaCleaner
from .models import BatchResult, DownloadedFile, DownloadOptions, DownloadResult, MediaInfo
from .providers import MediaProvider, default_providers
from .urls import detect_platform, normalize_url

log = logging.getLogger(__name__)

DEFAULT_TEMPLATE = "{platform}/{uploader}/{title} [{id}]"

#: Fields available in download filename templates.
DOWNLOAD_FIELDS = frozenset(
    {
        "id",
        "title",
        "uploader",
        "uploader_id",
        "platform",
        "date",  # upload date, YYYY-MM-DD
        "year",
        "month",
        "day",
        "media_type",
        "index",  # 1-based position inside a collection (None for single items)
        "collection",  # title of the playlist/gallery the item belongs to
        "collection_id",
        "height",
    }
)

_KIND_ORDER = {"media": 0, "subtitle": 1, "thumbnail": 2, "metadata": 3}


class MediaClient:
    """Fetch media from YouTube, TikTok, Instagram, X, Reddit, direct links... through one API.

    >>> client = MediaClient("downloads", options=DownloadOptions(quality="720p"))
    >>> info = client.info("https://youtu.be/dQw4w9WgXcQ")
    >>> result = client.download("https://youtu.be/dQw4w9WgXcQ?si=tracking")
    >>> result.primary
    PosixPath('downloads/youtube/Rick Astley/Never Gonna Give You Up [dQw4w9WgXcQ].mp4')

    Every download goes through the same pipeline regardless of provider:
    URL normalization -> provider fetch into a staging dir -> content-based type
    detection -> extension fixing -> metadata stripping -> template-based naming
    -> conflict-safe move into ``output_dir``.

    Only download content you have the right to download, and respect each
    platform's terms of service.

    Args:
        output_dir: Where finished files go.
        providers: Ordered providers; the first available one that supports a URL wins.
            Defaults to direct links, then yt-dlp.
        options: Default :class:`DownloadOptions`; override per call with keyword arguments.
        clean: :class:`CleanOptions`, ``True`` for defaults or ``False`` to keep files untouched.
        filename_template: Relative path template without extension (see :data:`DOWNLOAD_FIELDS`).
        conflict: ``rename``, ``skip`` or ``overwrite`` when a destination file exists.
        strip_tracking: Remove tracking parameters from URLs before fetching.
    """

    def __init__(
        self,
        output_dir: str | os.PathLike[str] = "downloads",
        *,
        providers: Iterable[MediaProvider] | None = None,
        options: DownloadOptions | None = None,
        clean: CleanOptions | bool = True,
        filename_template: str = DEFAULT_TEMPLATE,
        conflict: Literal["rename", "skip", "overwrite"] = "rename",
        strip_tracking: bool = True,
        detector: FileTypeDetector | None = None,
    ):
        if conflict not in ("rename", "skip", "overwrite"):
            raise ValueError(f"invalid conflict strategy {conflict!r}")
        validate_template(filename_template, DOWNLOAD_FIELDS)
        self.output_dir = Path(output_dir).expanduser()
        self.providers: list[MediaProvider] = list(providers) if providers is not None else default_providers()
        self.options = options or DownloadOptions()
        self.detector = detector or FileTypeDetector()
        if clean is True:
            clean = CleanOptions()
        self.clean_options: CleanOptions | None = clean or None
        self.cleaner = MediaCleaner(self.clean_options, self.detector) if self.clean_options else None
        self.filename_template = filename_template
        self.conflict = conflict
        self.strip_tracking = strip_tracking

    # -- providers -----------------------------------------------------------

    def register_provider(self, provider: MediaProvider, *, first: bool = True) -> None:
        """Add a provider; ``first=True`` gives it priority over the built-ins."""
        if first:
            self.providers.insert(0, provider)
        else:
            self.providers.append(provider)

    def resolve_provider(self, url: str) -> MediaProvider:
        url = normalize_url(url, strip_tracking=False)
        unavailable = []
        for provider in self.providers:
            if not provider.is_available():
                unavailable.append(provider.name)
                continue
            if provider.supports(url):
                return provider
        hint = ""
        if unavailable:
            hint = f" (unavailable providers: {', '.join(unavailable)}; try: pip install 'mediatrace[media]')"
        raise UnsupportedURLError(f"no provider can handle {url}{hint}")

    def supports(self, url: str) -> bool:
        try:
            self.resolve_provider(url)
        except MediaError:
            return False
        return True

    # -- public API ----------------------------------------------------------

    def normalize(self, url: str) -> str:
        return normalize_url(url, strip_tracking=self.strip_tracking)

    @staticmethod
    def detect_platform(url: str) -> str:
        return detect_platform(url)

    def _options(self, overrides: dict[str, Any]) -> DownloadOptions:
        if not overrides:
            return self.options
        try:
            return dataclasses.replace(self.options, **overrides)
        except TypeError as exc:
            raise TypeError(f"unknown download option: {exc}") from None

    def info(self, url: str, **overrides: Any) -> MediaInfo:
        """Fetch metadata (title, uploader, formats, gallery entries...) without downloading."""
        clean_url = self.normalize(url)
        provider = self.resolve_provider(clean_url)
        return provider.extract_info(clean_url, self._options(overrides))

    def download(
        self,
        url: str,
        *,
        output_dir: str | os.PathLike[str] | None = None,
        filename_template: str | None = None,
        **overrides: Any,
    ) -> DownloadResult:
        """Download, clean and file media from ``url``. Keyword arguments override :class:`DownloadOptions`."""
        options = self._options(overrides)
        template = filename_template or self.filename_template
        if filename_template:
            validate_template(template, DOWNLOAD_FIELDS)
        clean_url = self.normalize(url)
        provider = self.resolve_provider(clean_url)
        out = Path(output_dir).expanduser() if output_dir else self.output_dir
        out.mkdir(parents=True, exist_ok=True)

        staging = Path(tempfile.mkdtemp(prefix=".mediatrace-staging-", dir=out))
        try:
            info, staged = provider.download(clean_url, staging, options)
            files = self._finalize(info, staged, out, template, options)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return DownloadResult(url=url, normalized_url=clean_url, provider=provider.name, info=info, files=files)

    def download_many(self, urls: Iterable[str], *, max_workers: int = 3, **overrides: Any) -> BatchResult:
        """Download several URLs concurrently; failures are collected, not raised."""
        unique = list(dict.fromkeys(u.strip() for u in urls if u and u.strip()))
        batch = BatchResult()

        def _one(u: str) -> tuple[str, DownloadResult | Exception]:
            try:
                return u, self.download(u, **overrides)
            except Exception as exc:  # collect everything so one bad URL doesn't stop the batch
                log.warning("download failed for %s: %s", u, exc)
                return u, exc

        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
            for u, outcome in pool.map(_one, unique):
                if isinstance(outcome, Exception):
                    batch.errors[u] = outcome
                else:
                    batch.results.append(outcome)
        return batch

    # -- internals ---------------------------------------------------------

    def _fields(self, entry: MediaInfo, root: MediaInfo) -> dict[str, object]:
        in_collection = entry is not root and root.is_collection
        index = None
        if in_collection:
            for i, item in enumerate(root.entries, 1):
                if item is entry or (item.id and item.id == entry.id):
                    index = i
                    break
        d = entry.upload_date or root.upload_date
        return {
            "id": entry.id or root.id,
            "title": entry.title or root.title,
            "uploader": entry.uploader or root.uploader,
            "uploader_id": entry.uploader_id or root.uploader_id,
            "platform": entry.platform or root.platform,
            "date": d.isoformat() if d else None,
            "year": f"{d.year:04d}" if d else None,
            "month": f"{d.month:02d}" if d else None,
            "day": f"{d.day:02d}" if d else None,
            "media_type": entry.media_type,
            "index": index,
            "collection": root.title if in_collection else None,
            "collection_id": root.id if in_collection else None,
            "height": entry.height,
        }

    def _finalize(
        self,
        info: MediaInfo,
        staged: list[DownloadedFile],
        out: Path,
        template: str,
        options: DownloadOptions,
    ) -> list[DownloadedFile]:
        max_len = self.clean_options.max_name_length if self.clean_options else 150
        ascii_only = bool(self.clean_options and self.clean_options.ascii_names)
        bases: dict[int, Path] = {}  # id(entry) -> destination path without extension
        results: list[DownloadedFile] = []
        reserved: set[str] = set()

        for item in sorted(staged, key=lambda f: _KIND_ORDER.get(f.kind, 9)):
            path, steps = item.path, list(item.steps)
            if self.cleaner is not None:
                try:
                    cleaned = self.cleaner.clean(path)
                except DownloadError:
                    if item.kind == "media":
                        raise
                    log.warning("dropping unusable %s %s", item.kind, path.name)
                    continue
                path, steps = cleaned.path, steps + cleaned.steps

            entry = item.info
            base = bases.get(id(entry))
            if base is None:
                rel = render_template(
                    template, self._fields(entry, info), max_segment_length=max_len, ascii_only=ascii_only
                )
                base = out / rel
            label = f".{item.label}" if item.label else ""
            _, ext = split_name(path.name)
            dest = base.with_name(base.name + label + ext.lower())

            skipped = False
            if dest.exists() or path_key(dest) in reserved:
                if self.conflict == "skip":
                    skipped = True
                elif self.conflict == "rename" or path_key(dest) in reserved:
                    dest = unique_path(dest, reserved)
            if item.kind == "media" and id(entry) not in bases:
                # Sidecars follow the media file's final (possibly de-conflicted) name.
                bases[id(entry)] = dest.with_name(dest.name[: len(dest.name) - len(label + ext.lower())])

            if skipped:
                path.unlink(missing_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    dest.unlink()  # conflict == "overwrite"
                shutil.move(str(path), str(dest))
                reserved.add(path_key(dest))
            results.append(DownloadedFile(dest, entry, item.kind, item.label, steps, skipped))

        if options.write_metadata:
            results.extend(self._write_metadata(info, bases, results))
        return results

    def _write_metadata(
        self, info: MediaInfo, bases: dict[int, Path], results: list[DownloadedFile]
    ) -> list[DownloadedFile]:
        written = []
        entries = {id(r.info): r.info for r in results if r.kind == "media"}
        for key, entry in entries.items():
            base = bases.get(key)
            if base is None:
                continue
            dest = base.with_name(base.name + ".info.json")
            data = entry.to_dict()
            if entry is not info and info.is_collection:
                data["collection"] = {"id": info.id, "title": info.title, "url": info.url}
            dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            written.append(DownloadedFile(dest, entry, "metadata"))
        return written
