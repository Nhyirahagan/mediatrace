"""High-level file type detection combining signatures, text sniffing and extensions."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path, PurePath

from ..exceptions import DetectionError
from .categories import (
    EXTENSIONS,
    MIME_TO_EXT,
    TEXT_OVERRIDES,
    Category,
    category_from_mime,
    is_textual_extension,
)
from .signatures import HEAD_SIZE, Detector, Probe, TypeMatch, detect_binary
from .text import decode_text, sniff_text

log = logging.getLogger(__name__)

_COMPOUND_EXTENSIONS = ("tar.gz", "tar.bz2", "tar.xz", "tar.zst", "tar.lz4")

#: detected extension -> declared extensions that are legitimate refinements of it.
#: e.g. content says "generic ZIP" but the file is named ``.cbz``: trust the name.
REFINEMENTS: dict[str, frozenset[str]] = {
    k: frozenset(v.split())
    for k, v in {
        "zip": "zipx cbz xpi kmz whl nupkg ipa aar vsix 3mf sketch crx ora appx msix xapk jar war ear apk "
        "docx xlsx pptx odt ods odp epub usdz ibooks pages numbers key kra fig xd afdesign procreate",
        "docx": "docm dotx dotm",
        "xlsx": "xlsm xltx xltm",
        "pptx": "pptm potx ppsx",
        "jar": "war ear",
        "msix": "appx appxbundle msixbundle",
        "cfb": "doc dot xls xlt xla ppt pot pps msg msi msp vsd pub mpp docx xlsx pptx db one",
        "doc": "dot",
        "xls": "xlt xla",
        "ppt": "pot pps",
        "jpg": "jpeg jpe jfif jif",
        "tif": "tiff dng cr2 nef nrw arw srf sr2 orf pef srw 3fr erf kdc mos iiq rw2",
        "heic": "heif hif",
        "heif": "heic hif",
        "png": "apng",
        "mp4": "m4a m4v m4b m4p m4r mov qt 3gp 3g2 f4v mp4v mpg4",
        "mov": "qt mp4 m4v",
        "m4a": "m4b m4p m4r mp4",
        "mkv": "mka mks mk3d webm",
        "webm": "mkv mka weba",
        "ogg": "oga ogv opus spx ogx",
        "opus": "ogg oga",
        "oga": "ogg",
        "wav": "wave",
        "mp3": "mpga mp2",
        "aac": "adts m4a",
        "aiff": "aif aifc",
        "mid": "midi kar rmi",
        "wmv": "wma asf",
        "avi": "divx",
        "mpg": "mpeg mpe m1v m2v vob",
        "ts": "m2t mts m2ts",
        "m2ts": "mts ts",
        "exe": "scr efi sys com",
        "dll": "ocx cpl drv sys efi pyd mui ax",
        "o": "ko",
        "gz": "gzip tgz tar.gz svgz",
        "bz2": "tbz2 tbz tar.bz2",
        "xz": "txz tar.xz",
        "zst": "tzst tar.zst",
        "lz4": "tar.lz4",
        "rar": "cbr",
        "7z": "cb7",
        "tar": "cbt",
        "pdf": "ai",
        "ps": "eps epsf",
        "psd": "psb",
        "sqlite": "sqlite3 db db3 s3db sl3 gpkg mbtiles",
        "iso": "img udf",
        "ico": "cur",
        "ttf": "ttc",
        "h5": "hdf5 hdf",
        "djvu": "djv",
        "mobi": "prc azw azw3",
        "pkg": "mpkg xar",
    }.items()
}


def declared_extension(name: str) -> str:
    """Lowercase extension of a file name without the dot, aware of ``.tar.gz`` & co."""
    low = name.lower()
    for compound in _COMPOUND_EXTENSIONS:
        if low.endswith("." + compound):
            return compound
    return PurePath(low).suffix[1:]


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """What a file really is, according to its content."""

    path: Path | None
    mime_type: str
    extension: str
    category: Category
    confidence: float
    method: str
    description: str = ""
    declared_extension: str = ""
    extension_mismatch: bool = False
    size: int = 0

    @property
    def suggested_name(self) -> str | None:
        """File name with the extension corrected, or None if nothing needs fixing."""
        if not (self.extension_mismatch and self.extension and self.path):
            return None
        name = self.path.name
        if self.declared_extension and name.lower().endswith("." + self.declared_extension):
            name = name[: -(len(self.declared_extension) + 1)]
        return f"{name}.{self.extension}"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["path"] = str(self.path) if self.path else None
        data["category"] = self.category.value
        return data


class FileTypeDetector:
    """Content-based file type detection.

    Detection order:

    1. custom detectors registered via :meth:`register` / :meth:`register_signature`
    2. built-in binary signatures and container inspectors (ZIP/OOXML, OLE2, MP4/HEIC, RIFF...)
    3. text heuristics (JSON, XML/SVG/HTML, CSV, shebang scripts, subtitles, vCard...)
    4. libmagic, if ``use_libmagic=True`` and ``python-magic`` is installed
    5. the file extension

    The declared extension is then reconciled with the content: ``photo.jpeg``
    that is a JPEG keeps ``jpeg``; ``photo.jpg`` that is actually an HTML error
    page is reported as HTML with ``extension_mismatch=True``.
    """

    def __init__(self, *, use_libmagic: bool = False, head_size: int = HEAD_SIZE):
        self.use_libmagic = use_libmagic
        self.head_size = head_size
        self._custom: list[Detector] = []

    # -- extension points --------------------------------------------------

    def register(self, detector: Callable[[Probe], TypeMatch | None]) -> None:
        """Add a custom detector; custom detectors run before the built-ins, newest first."""
        self._custom.insert(0, detector)

    def register_signature(
        self,
        magic: bytes,
        extension: str,
        mime_type: str,
        category: Category | str,
        *,
        offset: int = 0,
        description: str = "",
        confidence: float = 0.95,
    ) -> None:
        """Teach the detector a new magic number."""
        found = TypeMatch(
            mime_type, extension.lower().lstrip("."), Category.parse(category), description, confidence, "signature"
        )

        def _detector(probe: Probe) -> TypeMatch | None:
            return found if probe.startswith(magic, offset) else None

        self.register(_detector)

    # -- public API ------------------------------------------------------

    def detect(self, path: str | os.PathLike[str]) -> DetectionResult:
        path = Path(path)
        try:
            if path.is_dir():
                raise DetectionError(f"{path} is a directory")
            probe = Probe(path=path, head_size=self.head_size)
        except DetectionError:
            raise
        except OSError as exc:
            raise DetectionError(f"cannot read {path}: {exc}") from exc
        return self._resolve(probe, path.name, path)

    def detect_bytes(self, data: bytes, filename: str | None = None) -> DetectionResult:
        probe = Probe(data=data, head_size=self.head_size)
        return self._resolve(probe, filename or "", None)

    def detect_many(
        self, paths: Iterable[str | os.PathLike[str]], *, max_workers: int | None = None
    ) -> dict[Path, DetectionResult]:
        """Detect many files in parallel. Unreadable files are logged and omitted."""
        path_list = [Path(p) for p in paths]
        results: dict[Path, DetectionResult] = {}

        def _one(p: Path) -> tuple[Path, DetectionResult | None]:
            try:
                return p, self.detect(p)
            except DetectionError as exc:
                log.warning("%s", exc)
                return p, None

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for p, result in pool.map(_one, path_list):
                if result is not None:
                    results[p] = result
        return results

    # -- internals ---------------------------------------------------------

    def _content_match(self, probe: Probe) -> TypeMatch | None:
        found = detect_binary(probe, self._custom)
        if found is not None:
            return found
        text = decode_text(probe.head, truncated=probe.size > len(probe.head))
        if text is not None:
            return sniff_text(text, probe)
        if self.use_libmagic:
            return self._libmagic(probe)
        return None

    def _libmagic(self, probe: Probe) -> TypeMatch | None:
        try:
            import magic  # type: ignore[import-not-found]
        except ImportError:
            return None
        try:
            mime = magic.from_buffer(probe.head, mime=True)
        except Exception:  # libmagic raises a variety of errors on odd input
            return None
        if not mime or mime in ("application/octet-stream", "application/x-empty"):
            return None
        return TypeMatch(mime, MIME_TO_EXT.get(mime, ""), category_from_mime(mime), "libmagic", 0.7, "libmagic")

    def _resolve(self, probe: Probe, name: str, path: Path | None) -> DetectionResult:
        declared = declared_extension(name)
        by_ext = EXTENSIONS.get(declared)

        def result(mime: str, ext: str, category: Category, conf: float, method: str, desc: str, mismatch: bool):
            return DetectionResult(
                path=path,
                mime_type=mime,
                extension=ext,
                category=category,
                confidence=round(conf, 3),
                method=method,
                description=desc,
                declared_extension=declared,
                extension_mismatch=mismatch,
                size=probe.size,
            )

        if probe.size == 0:
            if by_ext:
                return result(by_ext[0], declared, by_ext[1], 0.3, "extension", "Empty file", False)
            return result("application/x-empty", declared, Category.OTHER, 0.3, "empty", "Empty file", False)

        content = self._content_match(probe)

        if content is None:
            if by_ext:
                return result(by_ext[0], declared, by_ext[1], 0.4, "extension", "Identified by extension", False)
            return result(
                "application/octet-stream", declared, Category.OTHER, 0.1, "unknown", "Unknown binary data", False
            )

        if content.method == "text":
            # Text content: a textual extension is more specific than any sniff
            # (``.py`` vs "plain text", ``.ts`` TypeScript vs MPEG-TS).
            textual = TEXT_OVERRIDES.get(declared) or (by_ext if by_ext and is_textual_extension(declared) else None)
            if textual:
                conf = max(content.confidence, 0.85)
                return result(textual[0], declared, textual[1], conf, "text+extension", content.description, False)
            # A binary extension on text content (e.g. an HTML error page saved as .jpg).
            mismatch = bool(declared) and declared != content.extension
            return result(
                content.mime_type,
                content.extension,
                content.category,
                content.confidence,
                content.method,
                content.description,
                mismatch,
            )

        refinements = REFINEMENTS.get(content.extension, frozenset())
        if declared and (declared == content.extension or declared in refinements):
            mime, category = by_ext if by_ext else (content.mime_type, content.category)
            return result(
                mime, declared, category, content.confidence, content.method, content.description, False
            )

        if not declared:
            mismatch = False
        elif not content.extension:
            # No canonical extension (e.g. ELF binaries): only flag clear category conflicts.
            mismatch = by_ext is not None and by_ext[1] is not content.category
        else:
            mismatch = True
        return result(
            content.mime_type,
            content.extension,
            content.category,
            content.confidence,
            content.method,
            content.description,
            mismatch,
        )


_default_detector: FileTypeDetector | None = None


def detect(path: str | os.PathLike[str]) -> DetectionResult:
    """Detect a file's type with a shared default :class:`FileTypeDetector`."""
    global _default_detector
    if _default_detector is None:
        _default_detector = FileTypeDetector()
    return _default_detector.detect(path)
