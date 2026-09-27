"""Unified media fetching and cleaning."""

from .cleaning import CleanOptions, CleanResult, MediaCleaner
from .client import DEFAULT_TEMPLATE, DOWNLOAD_FIELDS, MediaClient
from .models import (
    BatchResult,
    DownloadedFile,
    DownloadOptions,
    DownloadResult,
    MediaFormat,
    MediaInfo,
    ProgressEvent,
)
from .providers import DirectProvider, MediaProvider, YtDlpProvider, default_providers
from .urls import detect_platform, extract_urls, is_url, normalize_url

__all__ = [
    "DEFAULT_TEMPLATE",
    "DOWNLOAD_FIELDS",
    "BatchResult",
    "CleanOptions",
    "CleanResult",
    "DirectProvider",
    "DownloadOptions",
    "DownloadResult",
    "DownloadedFile",
    "MediaCleaner",
    "MediaClient",
    "MediaFormat",
    "MediaInfo",
    "MediaProvider",
    "ProgressEvent",
    "YtDlpProvider",
    "default_providers",
    "detect_platform",
    "extract_urls",
    "is_url",
    "normalize_url",
]
