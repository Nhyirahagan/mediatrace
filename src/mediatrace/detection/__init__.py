"""Content-based file type detection."""

from .categories import CATEGORY_FOLDERS, EXTENSIONS, MEDIA_CATEGORIES, MIME_TO_EXT, Category, lookup_extension
from .detector import DetectionResult, FileTypeDetector, declared_extension, detect
from .signatures import Probe, TypeMatch

__all__ = [
    "CATEGORY_FOLDERS",
    "EXTENSIONS",
    "MEDIA_CATEGORIES",
    "MIME_TO_EXT",
    "Category",
    "DetectionResult",
    "FileTypeDetector",
    "Probe",
    "TypeMatch",
    "declared_extension",
    "detect",
    "lookup_extension",
]
