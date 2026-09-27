"""Binary signature ("magic number") detection.

Detection functions take a :class:`Probe` and return a :class:`TypeMatch` or
``None``. Structured formats (ZIP/OOXML, OLE2, ISO-BMFF, RIFF, EBML, Ogg, PE,
ELF...) get dedicated inspectors that look past the first few bytes, so a
``.docx`` is told apart from a ``.zip`` and a HEIC photo from an MP4 video.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .categories import EXTENSIONS, Category

HEAD_SIZE = 8192


class Probe:
    """Lazy random-access view over a file or an in-memory buffer."""

    def __init__(self, path: Path | None = None, data: bytes | None = None, head_size: int = HEAD_SIZE):
        if path is not None:
            self.path: Path | None = Path(path)
            self._data: bytes | None = None
            self.size = self.path.stat().st_size
            with open(self.path, "rb") as fh:
                self.head = fh.read(head_size)
        else:
            self.path = None
            self._data = data or b""
            self.size = len(self._data)
            self.head = self._data[:head_size]

    def read_at(self, offset: int, length: int) -> bytes:
        if offset < 0 or length <= 0:
            return b""
        end = offset + length
        if end <= len(self.head):
            return self.head[offset:end]
        if self._data is not None:
            return self._data[offset:end]
        if offset >= self.size:
            return b""
        assert self.path is not None
        with open(self.path, "rb") as fh:
            fh.seek(offset)
            return fh.read(length)

    def startswith(self, magic: bytes, offset: int = 0) -> bool:
        return self.read_at(offset, len(magic)) == magic

    def open(self) -> BinaryIO:
        if self._data is not None:
            return io.BytesIO(self._data)
        assert self.path is not None
        return open(self.path, "rb")


@dataclass(frozen=True, slots=True)
class TypeMatch:
    mime_type: str
    extension: str
    category: Category
    description: str = ""
    confidence: float = 0.95
    method: str = "signature"


Detector = Callable[[Probe], "TypeMatch | None"]


def match(
    ext: str,
    description: str = "",
    confidence: float = 0.95,
    *,
    mime: str | None = None,
    category: Category | None = None,
    method: str = "signature",
) -> TypeMatch:
    """Build a TypeMatch, filling MIME type and category from the extension table."""
    known = EXTENSIONS.get(ext)
    return TypeMatch(
        mime_type=mime or (known[0] if known else "application/octet-stream"),
        extension=ext,
        category=category or (known[1] if known else Category.OTHER),
        description=description,
        confidence=confidence,
        method=method,
    )


# --------------------------------------------------------------------------- #
# Plain magic-number table: (offset, magic, extension, description, confidence)
# --------------------------------------------------------------------------- #

_SIMPLE: list[tuple[int, bytes, str, str, float]] = [
    (0, b"\x89PNG\r\n\x1a\n", "png", "PNG image", 0.99),
    (0, b"\xff\xd8\xff", "jpg", "JPEG image", 0.97),
    (0, b"GIF87a", "gif", "GIF image", 0.99),
    (0, b"GIF89a", "gif", "GIF image", 0.99),
    (0, b"II*\x00", "tif", "TIFF image (little-endian)", 0.9),
    (0, b"MM\x00*", "tif", "TIFF image (big-endian)", 0.9),
    (0, b"II+\x00", "tif", "BigTIFF image", 0.9),
    (0, b"MM\x00+", "tif", "BigTIFF image", 0.9),
    (0, b"8BPS\x00\x02", "psb", "Photoshop large document", 0.99),
    (0, b"8BPS", "psd", "Photoshop document", 0.97),
    (0, b"\x00\x00\x00\x0cJXL \r\n\x87\n", "jxl", "JPEG XL image", 0.99),
    (0, b"\xff\x0a", "jxl", "JPEG XL codestream", 0.6),
    (0, b"\x00\x00\x00\x0cjP  \r\n\x87\n", "jp2", "JPEG 2000 image", 0.99),
    (0, b"qoif", "qoi", "QOI image", 0.95),
    (0, b"v/1\x01", "exr", "OpenEXR image", 0.95),
    (0, b"#?RADIANCE", "hdr", "Radiance HDR image", 0.95),
    (0, b"DDS ", "dds", "DirectDraw surface", 0.9),
    (0, b"gimp xcf", "xcf", "GIMP image", 0.99),
    (0, b"FUJIFILMCCD-RAW", "raf", "Fujifilm RAW image", 0.99),
    (0, b"%PDF-", "pdf", "PDF document", 0.99),
    (0, b"{\\rtf", "rtf", "Rich Text document", 0.97),
    (0, b"\xc5\xd0\xd3\xc6", "eps", "Encapsulated PostScript (binary header)", 0.95),
    (0, b"%!PS-Adobe-3.0 EPSF", "eps", "Encapsulated PostScript", 0.97),
    (0, b"%!PS", "ps", "PostScript document", 0.95),
    (0, b"AT&TFORM", "djvu", "DjVu document", 0.97),
    (60, b"BOOKMOBI", "mobi", "Mobipocket e-book", 0.97),
    (0, b"Rar!\x1a\x07\x01\x00", "rar", "RAR archive (v5)", 0.99),
    (0, b"Rar!\x1a\x07\x00", "rar", "RAR archive (v4)", 0.99),
    (0, b"7z\xbc\xaf\x27\x1c", "7z", "7-Zip archive", 0.99),
    (0, b"\x1f\x8b\x08", "gz", "gzip compressed data", 0.97),
    (0, b"\xfd7zXZ\x00", "xz", "XZ compressed data", 0.99),
    (0, b"\x28\xb5\x2f\xfd", "zst", "Zstandard compressed data", 0.97),
    (0, b"\x04\x22\x4d\x18", "lz4", "LZ4 frame", 0.95),
    (0, b"MSCF\x00\x00\x00\x00", "cab", "Microsoft Cabinet archive", 0.97),
    (0, b"!<arch>\ndebian", "deb", "Debian package", 0.99),
    (0, b"\xed\xab\xee\xdb", "rpm", "RPM package", 0.97),
    (0, b"xar!", "pkg", "XAR archive (macOS installer package)", 0.9),
    (257, b"ustar", "tar", "POSIX tar archive", 0.97),
    (0, b"fLaC", "flac", "FLAC audio", 0.99),
    (0, b"ID3", "mp3", "MP3 audio (ID3 tagged)", 0.9),
    (0, b"MThd", "mid", "MIDI audio", 0.97),
    (0, b"#!AMR", "amr", "AMR audio", 0.97),
    (0, b"MAC ", "ape", "Monkey's Audio", 0.9),
    (0, b"wvpk", "wv", "WavPack audio", 0.97),
    (0, b"caff", "caf", "Core Audio Format", 0.95),
    (0, b".snd", "au", "Sun/NeXT audio", 0.9),
    (0, b"0&\xb2u\x8ef\xcf\x11\xa6\xd9\x00\xaa\x00b\xcel", "wmv", "ASF / Windows Media container", 0.95),
    (0, b"FLV\x01", "flv", "Flash video", 0.97),
    (0, b".RMF", "rm", "RealMedia", 0.95),
    (0, b"\x00\x00\x01\xba", "mpg", "MPEG program stream", 0.9),
    (0, b"\x00\x00\x01\xb3", "mpg", "MPEG video stream", 0.9),
    (0, b"wOFF", "woff", "WOFF font", 0.99),
    (0, b"wOF2", "woff2", "WOFF2 font", 0.99),
    (0, b"OTTO", "otf", "OpenType font", 0.95),
    (0, b"ttcf", "ttc", "TrueType font collection", 0.97),
    (0, b"\x00\x01\x00\x00Standard Jet DB", "mdb", "Microsoft Access database", 0.99),
    (0, b"\x00\x01\x00\x00Standard ACE DB", "accdb", "Microsoft Access database", 0.99),
    (0, b"\x00\x01\x00\x00\x00", "ttf", "TrueType font", 0.75),
    (0, b"SQLite format 3\x00", "sqlite", "SQLite 3 database", 0.99),
    (0, b"\x03\xd9\xa2\x9a\x67\xfb\x4b\xb5", "kdbx", "KeePass database", 0.99),
    (0, b"PAR1", "parquet", "Apache Parquet data", 0.9),
    (0, b"Obj\x01", "avro", "Apache Avro data", 0.9),
    (0, b"ARROW1", "arrow", "Apache Arrow data", 0.95),
    (0, b"\x89HDF\r\n\x1a\n", "h5", "HDF5 data", 0.99),
    (0, b"\x93NUMPY", "npy", "NumPy array", 0.99),
    (0, b"\x00asm", "wasm", "WebAssembly binary", 0.97),
    (0, b"dex\n", "dex", "Android DEX bytecode", 0.97),
    (0, b"conectix", "vhd", "Virtual PC disk image", 0.97),
    (0, b"vhdxfile", "vhdx", "Hyper-V disk image", 0.99),
    (0, b"KDMV", "vmdk", "VMware disk image", 0.95),
    (0, b"QFI\xfb", "qcow2", "QEMU disk image", 0.97),
    (64, b"\x7f\x10\xda\xbe", "vdi", "VirtualBox disk image", 0.95),
    (0, b"MSWIM\x00\x00\x00", "wim", "Windows Imaging archive", 0.99),
    (32769, b"CD001", "iso", "ISO 9660 disc image", 0.97),
    (0, b"d8:announce", "torrent", "BitTorrent metainfo", 0.97),
    (0, b"d13:announce-list", "torrent", "BitTorrent metainfo", 0.97),
    (0, b"glTF", "glb", "glTF binary model", 0.97),
    (0, b"BLENDER", "blend", "Blender project", 0.99),
    (0, b"Kaydara FBX Binary", "fbx", "Autodesk FBX model", 0.99),
    (0, b"AC10", "dwg", "AutoCAD drawing", 0.85),
    (0, b"L\x00\x00\x00\x01\x14\x02\x00", "lnk", "Windows shortcut", 0.99),
    (0, b"\x00\x00\x01\x00", "ico", "Windows icon", 0.6),
    (0, b"\x00\x00\x02\x00", "cur", "Windows cursor", 0.6),
]
# Longest magic first so that more specific signatures win.
_SIMPLE.sort(key=lambda row: len(row[1]), reverse=True)


def _simple(probe: Probe) -> TypeMatch | None:
    for offset, magic, ext, desc, conf in _SIMPLE:
        if probe.startswith(magic, offset):
            return match(ext, desc, conf)
    return None


# --------------------------------------------------------------------------- #
# Structured-format inspectors
# --------------------------------------------------------------------------- #

_ODF_MIMETYPES = {
    "application/epub+zip": "epub",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.text-template": "ott",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.spreadsheet-template": "ots",
    "application/vnd.oasis.opendocument.presentation": "odp",
    "application/vnd.oasis.opendocument.presentation-template": "otp",
    "application/x-krita": "kra",
    "image/openraster": "ora",
}


def classify_zip(names: list[str], mimetype: str | None = None) -> TypeMatch:
    """Identify a ZIP-based container format from its member list."""
    nameset = set(names)
    if mimetype and mimetype in _ODF_MIMETYPES:
        ext = _ODF_MIMETYPES[mimetype]
        return match(ext, f"{ext.upper()} document (ZIP container)", 0.99, method="container")

    def any_prefix(prefix: str) -> bool:
        return any(n.startswith(prefix) for n in names)

    if "[Content_Types].xml" in nameset:
        macro = any(n.endswith("vbaProject.bin") for n in names)
        if any_prefix("word/"):
            return match("docm" if macro else "docx", "Microsoft Word document", 0.99, method="container")
        if any_prefix("xl/"):
            return match("xlsm" if macro else "xlsx", "Microsoft Excel workbook", 0.99, method="container")
        if any_prefix("ppt/"):
            return match("pptm" if macro else "pptx", "Microsoft PowerPoint presentation", 0.99, method="container")
        if any_prefix("visio/"):
            return match("vsdx", "Microsoft Visio drawing", 0.99, method="container")
        if "3D/3dmodel.model" in nameset:
            return match("3mf", "3D Manufacturing Format model", 0.99, method="container")
        if "AppxManifest.xml" in nameset:
            return match("msix", "Windows app package", 0.97, method="container")
    if "AndroidManifest.xml" in nameset and any(n.endswith(".dex") for n in names):
        return match("apk", "Android application package", 0.99, method="container")
    if "BundleConfig.pb" in nameset:
        return match("aab", "Android app bundle", 0.97, method="container")
    if any(n.startswith("Payload/") and ".app/" in n for n in names):
        return match("ipa", "iOS application archive", 0.97, method="container")
    if any(n.endswith(".dist-info/WHEEL") for n in names):
        return match("whl", "Python wheel", 0.99, method="container")
    if any(n.endswith(".nuspec") and "/" not in n for n in names):
        return match("nupkg", "NuGet package", 0.97, method="container")
    if "META-INF/MANIFEST.MF" in nameset or any(n.endswith(".class") for n in names):
        if any_prefix("WEB-INF/"):
            return match("war", "Java web archive", 0.97, method="container")
        return match("jar", "Java archive", 0.95, method="container")
    if "doc.kml" in nameset:
        return match("kmz", "Google Earth KMZ", 0.97, method="container")
    if "document.json" in nameset and "meta.json" in nameset and any_prefix("pages/"):
        return match("sketch", "Sketch design file", 0.95, method="container")
    if "Index/Document.iwa" in nameset:
        return match("zip", "Apple iWork document", 0.8, method="container")
    return match("zip", "ZIP archive", 0.95, method="container")


def _zip(probe: Probe) -> TypeMatch | None:
    if not (probe.startswith(b"PK\x03\x04") or probe.startswith(b"PK\x05\x06") or probe.startswith(b"PK\x07\x08")):
        return None
    try:
        with probe.open() as fh, zipfile.ZipFile(fh) as zf:
            names = zf.namelist()
            mimetype = None
            if "mimetype" in names:
                try:
                    mimetype = zf.read("mimetype")[:128].decode("ascii", "ignore").strip()
                except (KeyError, zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError):
                    mimetype = None
    except (zipfile.BadZipFile, OSError, ValueError, EOFError):
        # Truncated or partially downloaded archive: the local header is still telling.
        head = probe.head
        if b"word/" in head and b"[Content_Types].xml" in head:
            return match("docx", "Microsoft Word document (damaged)", 0.7, method="container")
        if b"mimetypeapplication/epub+zip" in head:
            return match("epub", "EPUB e-book (damaged)", 0.7, method="container")
        return match("zip", "ZIP archive (damaged or truncated)", 0.7, method="container")
    return classify_zip(names, mimetype)


def _ole(probe: Probe) -> TypeMatch | None:
    if not probe.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return None
    blob = probe.read_at(0, min(probe.size, 2_000_000))

    def has(stream: str) -> bool:
        return stream.encode("utf-16-le") in blob

    if has("EncryptedPackage"):
        return match("cfb", "Encrypted Office document", 0.8, category=Category.DOCUMENT, method="container")
    if has("WordDocument"):
        return match("doc", "Microsoft Word 97-2003 document", 0.97, method="container")
    if has("PowerPoint Document"):
        return match("ppt", "Microsoft PowerPoint 97-2003 presentation", 0.97, method="container")
    if has("Workbook") or has("Book"):
        return match("xls", "Microsoft Excel 97-2003 workbook", 0.95, method="container")
    if has("__substg1.0_"):
        return match("msg", "Outlook message", 0.95, method="container")
    if has("VisioDocument"):
        return match("vsd", "Microsoft Visio drawing", 0.95, method="container")
    return match("cfb", "Microsoft Compound File (OLE2)", 0.6, category=Category.OTHER, method="container")


def _ftyp(probe: Probe) -> TypeMatch | None:
    """ISO base media file format: MP4, MOV, M4A, HEIC, AVIF, 3GP, CR3..."""
    if probe.read_at(4, 4) != b"ftyp":
        return None
    box_size = int.from_bytes(probe.read_at(0, 4), "big")
    major = probe.read_at(8, 4)
    compat = {probe.read_at(i, 4) for i in range(16, min(max(box_size, 16), 256), 4)}
    brands = compat | {major}

    if brands & {b"avif", b"avis"}:
        return match("avif", "AVIF image", 0.98)
    if major in (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis") or (
        major in (b"mif1", b"msf1") and brands & {b"heic", b"heix"}
    ):
        return match("heic", "HEIC image", 0.98)
    if major in (b"mif1", b"msf1"):
        return match("heif", "HEIF image", 0.95)
    if major == b"crx ":
        return match("cr3", "Canon CR3 RAW image", 0.98)
    if major == b"M4A ":
        return match("m4a", "MPEG-4 audio", 0.97)
    if major == b"M4B ":
        return match("m4b", "MPEG-4 audiobook", 0.97)
    if major == b"M4P ":
        return match("m4p", "Protected MPEG-4 audio", 0.97)
    if major in (b"M4V ", b"M4VH", b"M4VP"):
        return match("m4v", "MPEG-4 video (iTunes)", 0.97)
    if major == b"qt  ":
        return match("mov", "QuickTime movie", 0.97)
    if major.startswith(b"3g2"):
        return match("3g2", "3GPP2 video", 0.95)
    if major.startswith((b"3gp", b"3ge", b"3gg")):
        return match("3gp", "3GPP video", 0.95)
    if major == b"f4v ":
        return match("f4v", "Flash MP4 video", 0.95)
    return match("mp4", "MPEG-4 video", 0.95)


def _riff(probe: Probe) -> TypeMatch | None:
    if probe.startswith(b"RIFF"):
        form = probe.read_at(8, 4)
        found = {
            b"WAVE": ("wav", "WAVE audio"),
            b"AVI ": ("avi", "AVI video"),
            b"WEBP": ("webp", "WebP image"),
            b"RMID": ("mid", "RIFF MIDI audio"),
        }.get(form)
        if found:
            return match(found[0], found[1], 0.98)
        return None
    if probe.startswith(b"RF64") and probe.read_at(8, 4) == b"WAVE":
        return match("wav", "RF64 WAVE audio", 0.97)
    if probe.startswith(b"FORM") and probe.read_at(8, 4) in (b"AIFF", b"AIFC"):
        return match("aiff", "AIFF audio", 0.98)
    return None


def _ebml(probe: Probe) -> TypeMatch | None:
    if not probe.startswith(b"\x1a\x45\xdf\xa3"):
        return None
    head = probe.read_at(0, 4096)
    if b"webm" in head:
        return match("webm", "WebM video", 0.97)
    return match("mkv", "Matroska video", 0.95)


def _ogg(probe: Probe) -> TypeMatch | None:
    if not probe.startswith(b"OggS"):
        return None
    head = probe.read_at(0, 8192)
    if b"OpusHead" in head:
        return match("opus", "Opus audio (Ogg)", 0.98)
    if b"\x80theora" in head:
        return match("ogv", "Theora video (Ogg)", 0.97)
    if b"\x01vorbis" in head:
        return match("ogg", "Vorbis audio (Ogg)", 0.98)
    if b"\x7fFLAC" in head:
        return match("oga", "FLAC audio (Ogg)", 0.95)
    if b"Speex   " in head:
        return match("spx", "Speex audio (Ogg)", 0.95)
    return match("ogg", "Ogg container", 0.85)


def _pe(probe: Probe) -> TypeMatch | None:
    if not probe.startswith(b"MZ") or probe.size < 64:
        return None
    e_lfanew = int.from_bytes(probe.read_at(0x3C, 4), "little")
    if 0 < e_lfanew < probe.size - 24 and probe.read_at(e_lfanew, 4) == b"PE\x00\x00":
        characteristics = int.from_bytes(probe.read_at(e_lfanew + 22, 2), "little")
        if characteristics & 0x2000:
            return match("dll", "Windows dynamic-link library", 0.97)
        return match("exe", "Windows executable", 0.97)
    return match("exe", "MS-DOS executable", 0.6)


def _elf(probe: Probe) -> TypeMatch | None:
    if not probe.startswith(b"\x7fELF"):
        return None
    order = "little" if probe.read_at(5, 1) == b"\x01" else "big"
    e_type = int.from_bytes(probe.read_at(16, 2), order)  # type: ignore[arg-type]
    kinds = {
        1: ("o", "ELF relocatable object"),
        2: ("", "ELF executable"),
        3: ("", "ELF shared object / PIE executable"),
        4: ("", "ELF core dump"),
    }
    ext, desc = kinds.get(e_type, ("", "ELF binary"))
    mime = "application/x-object" if ext == "o" else "application/x-executable"
    return match(ext, desc, 0.97, mime=mime, category=Category.EXECUTABLE)


_MACHO = {b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"}


def _macho_or_java(probe: Probe) -> TypeMatch | None:
    magic = probe.read_at(0, 4)
    if magic in _MACHO:
        return match("", "Mach-O binary", 0.97, mime="application/x-mach-binary", category=Category.EXECUTABLE)
    if magic == b"\xca\xfe\xba\xbe":
        # Shared by Java class files and Mach-O universal binaries; the next
        # field is the arch count (small) or the class-file version (>= 45).
        value = int.from_bytes(probe.read_at(4, 4), "big")
        if value < 45:
            return match(
                "", "Mach-O universal binary", 0.9, mime="application/x-mach-binary", category=Category.EXECUTABLE
            )
        return match("class", "Java class file", 0.95)
    return None


def _bmp(probe: Probe) -> TypeMatch | None:
    if probe.startswith(b"BM") and probe.size > 26:
        dib_header = int.from_bytes(probe.read_at(14, 4), "little")
        if dib_header in (12, 40, 52, 56, 64, 108, 124):
            return match("bmp", "BMP image", 0.95)
    return None


def _bzip2(probe: Probe) -> TypeMatch | None:
    if probe.startswith(b"BZh") and probe.read_at(3, 1) in b"123456789" and probe.read_at(4, 6) == b"1AY&SY":
        return match("bz2", "bzip2 compressed data", 0.97)
    return None


def _looks_like_utf16le(data: bytes) -> bool:
    body = data[2:] if data.startswith(b"\xff\xfe") else data
    body = body[: len(body) - len(body) % 2]
    if len(body) < 2:
        return False
    try:
        text = body.decode("utf-16-le")
    except UnicodeDecodeError:
        return False
    printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in text)
    return printable / len(text) > 0.95


def _mpeg_audio(probe: Probe) -> TypeMatch | None:
    head = probe.read_at(0, 3)
    if len(head) < 2 or head[0] != 0xFF or (head[1] & 0xE0) != 0xE0:
        return None
    # FF FE is also the UTF-16 LE byte-order mark; don't mistake BOM-prefixed
    # text for a Layer I frame.
    if head[1] == 0xFE and _looks_like_utf16le(probe.read_at(0, 512)):
        return None
    layer = (head[1] >> 1) & 0b11
    # Frame headers with a "bad" bitrate index or reserved sample rate are invalid.
    if layer and len(head) == 3 and ((head[2] >> 4) == 0xF or ((head[2] >> 2) & 0b11) == 0b11):
        return None
    if layer == 0 and (head[1] & 0xF6) == 0xF0:
        return match("aac", "AAC audio (ADTS)", 0.75)
    if layer == 1:
        return match("mp3", "MP3 audio", 0.75)
    if layer == 2:
        return match("mp2", "MPEG Layer II audio", 0.7)
    if layer == 3:
        return match("mp3", "MPEG Layer I audio", 0.6)
    return None


def _mpeg_ts(probe: Probe) -> TypeMatch | None:
    if b"\x00" not in probe.head[:4096]:
        return None  # real transport streams are full of NUL bytes; avoids matching text
    if probe.size >= 188 * 3 and all(probe.read_at(i * 188, 1) == b"G" for i in range(3)):
        return match("ts", "MPEG transport stream", 0.9)
    if probe.size >= 192 * 3 and all(probe.read_at(4 + i * 192, 1) == b"G" for i in range(3)):
        return match("m2ts", "MPEG-2 transport stream (BDAV)", 0.9)
    return None


def _trailers(probe: Probe) -> TypeMatch | None:
    """Formats identified by a footer rather than a header."""
    if probe.size > 1024:
        tail = probe.read_at(probe.size - 512, 8)
        if tail.startswith(b"koly"):
            return match("dmg", "Apple disk image", 0.95)
        if tail == b"conectix":
            return match("vhd", "Virtual PC disk image (fixed)", 0.95)
    return None


#: Ordered detectors; structured inspectors run before the plain table.
BUILTIN_DETECTORS: list[Detector] = [
    _zip,
    _ole,
    _ftyp,
    _riff,
    _ebml,
    _ogg,
    _pe,
    _elf,
    _macho_or_java,
    _bmp,
    _bzip2,
    _simple,
    _mpeg_ts,
    _mpeg_audio,
    _trailers,
]


def detect_binary(probe: Probe, extra: list[Detector] | None = None) -> TypeMatch | None:
    """Run custom detectors first, then built-ins; return the first match."""
    for detector in (*(extra or ()), *BUILTIN_DETECTORS):
        try:
            found = detector(probe)
        except (OSError, ValueError):
            continue
        if found is not None:
            return found
    return None
