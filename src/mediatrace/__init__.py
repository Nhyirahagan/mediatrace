"""mediatrace - smart file organizer and unified media downloader core.

Quick start::

    from mediatrace import FileOrganizer, MediaClient, detect, preset

    detect("mystery.bin").mime_type          # content-based type detection
    FileOrganizer("~/Sorted", rules=preset("media-timeline")).organize("~/Downloads", dry_run=True)
    MediaClient("downloads").download("https://www.youtube.com/watch?v=...")
"""

from __future__ import annotations

import logging

from .detection import Category, DetectionResult, FileTypeDetector, detect
from .exceptions import (
    DetectionError,
    DownloadError,
    InvalidURLError,
    JournalError,
    MediaError,
    MediaTraceError,
    OrganizerError,
    ProviderUnavailableError,
    RuleError,
    TemplateError,
    UnsupportedURLError,
)
from .media import (
    CleanOptions,
    DirectProvider,
    DownloadOptions,
    DownloadResult,
    MediaClient,
    MediaInfo,
    MediaProvider,
    YtDlpProvider,
    detect_platform,
    extract_urls,
    normalize_url,
)
from .naming import render_template, sanitize_filename
from .organizer import (
    Action,
    FileOrganizer,
    FolderWatcher,
    OrganizePlan,
    OrganizeReport,
    Rule,
    load_rules,
    preset,
)

__version__ = "0.1.0"

logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = [
    "Action",
    "Category",
    "CleanOptions",
    "DetectionError",
    "DetectionResult",
    "DirectProvider",
    "DownloadError",
    "DownloadOptions",
    "DownloadResult",
    "FileOrganizer",
    "FileTypeDetector",
    "FolderWatcher",
    "InvalidURLError",
    "JournalError",
    "MediaClient",
    "MediaError",
    "MediaInfo",
    "MediaProvider",
    "MediaTraceError",
    "OrganizePlan",
    "OrganizeReport",
    "OrganizerError",
    "ProviderUnavailableError",
    "Rule",
    "RuleError",
    "TemplateError",
    "UnsupportedURLError",
    "YtDlpProvider",
    "__version__",
    "detect",
    "detect_platform",
    "extract_urls",
    "load_rules",
    "normalize_url",
    "preset",
    "render_template",
    "sanitize_filename",
]
