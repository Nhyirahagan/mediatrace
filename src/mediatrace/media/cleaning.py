"""Post-download cleaning: real-type detection, extension fixing, metadata stripping."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..detection import Category, DetectionResult, FileTypeDetector
from ..exceptions import DownloadError
from ..imaging import convert_image, strip_av_metadata, strip_image_metadata
from ..naming import split_name, unique_path

log = logging.getLogger(__name__)

_HTML_MIMES = frozenset({"text/html", "application/xhtml+xml"})


@dataclass(slots=True)
class CleanOptions:
    """What the cleaning pipeline does to every downloaded file.

    Metadata stripping needs Pillow (images) and ffmpeg (audio/video); when a
    tool is missing that step is skipped and simply not listed in ``steps``.
    """

    fix_extensions: bool = True  # rename .jpg that is really WebP to .webp, etc.
    strip_metadata: bool = True  # remove EXIF/GPS from images
    strip_av_metadata: bool = True  # remove container tags from audio/video (ffmpeg, stream copy)
    image_format: str | None = None  # convert images, e.g. "jpg" / "png" / "webp"
    image_quality: int = 92
    reject_html: bool = True  # fail when a "media" file is an HTML error/login page
    sanitize_names: bool = True
    ascii_names: bool = False
    max_name_length: int = 150


@dataclass(slots=True)
class CleanResult:
    path: Path
    detection: DetectionResult
    steps: list[str] = field(default_factory=list)


class MediaCleaner:
    def __init__(self, options: CleanOptions | None = None, detector: FileTypeDetector | None = None):
        self.options = options or CleanOptions()
        self.detector = detector or FileTypeDetector()

    def clean(self, path: str | Path) -> CleanResult:
        path = Path(path)
        opts = self.options
        detection = self.detector.detect(path)
        steps: list[str] = []

        if opts.reject_html and detection.mime_type in _HTML_MIMES:
            raise DownloadError(
                f"{path.name} is an HTML page, not media (the site may require login or blocked the request)"
            )

        if opts.fix_extensions and detection.extension and detection.extension_mismatch:
            stem, _ = split_name(path.name)
            fixed = unique_path(path.with_name(f"{stem}.{detection.extension}"))
            path = path.rename(fixed)
            steps.append(f"extension:{detection.declared_extension or '-'}->{detection.extension}")
            detection = self.detector.detect(path)

        if detection.category is Category.IMAGE:
            if opts.image_format and detection.extension not in _aliases(opts.image_format):
                converted = convert_image(path, opts.image_format, quality=opts.image_quality)
                if converted is not None:
                    steps.append(f"converted:{detection.extension}->{converted.suffix.lstrip('.')}")
                    path = converted
                    detection = self.detector.detect(path)
            if opts.strip_metadata and strip_image_metadata(path):
                steps.append("stripped-image-metadata")
        elif detection.category in (Category.VIDEO, Category.AUDIO):
            if opts.strip_av_metadata and strip_av_metadata(path):
                steps.append("stripped-av-metadata")

        return CleanResult(path=path, detection=detection, steps=steps)


def _aliases(fmt: str) -> set[str]:
    fmt = fmt.lower().lstrip(".")
    return {"jpg", "jpeg"} if fmt in ("jpg", "jpeg") else {fmt}
