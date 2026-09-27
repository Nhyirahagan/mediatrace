from __future__ import annotations

import io
import json
import struct
import zipfile
from pathlib import Path

import pytest

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 64
GIF = b"GIF89a" + b"\x00" * 64
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n"
WEBP = b"RIFF" + struct.pack("<I", 100) + b"WEBPVP8 " + b"\x00" * 64
WAV = b"RIFF" + struct.pack("<I", 100) + b"WAVEfmt " + b"\x00" * 64
MP4 = struct.pack(">I", 24) + b"ftypisom" + b"\x00\x00\x02\x00isomiso2" + b"\x00" * 64
HEIC = struct.pack(">I", 24) + b"ftypheic" + b"\x00\x00\x00\x00mif1heic" + b"\x00" * 64
MOV = struct.pack(">I", 20) + b"ftypqt  " + b"\x00\x00\x02\x00qt  " + b"\x00" * 64
M4A = struct.pack(">I", 20) + b"ftypM4A " + b"\x00\x00\x02\x00M4A " + b"\x00" * 64
MKV = b"\x1a\x45\xdf\xa3" + b"\x00" * 8 + b"B\x82\x88matroska" + b"\x00" * 32
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 8 + b"B\x82\x84webm" + b"\x00" * 32
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 16
FLAC = b"fLaC" + b"\x00" * 64
OGG_OPUS = b"OggS\x00\x02" + b"\x00" * 20 + b"OpusHead" + b"\x00" * 32
GZIP = b"\x1f\x8b\x08\x00" + b"\x00" * 32
SEVENZ = b"7z\xbc\xaf\x27\x1c" + b"\x00" * 32
SQLITE = b"SQLite format 3\x00" + b"\x00" * 84
ELF = b"\x7fELF\x02\x01\x01" + b"\x00" * 9 + b"\x02\x00" + b"\x00" * 46


def pe_exe(dll: bool = False) -> bytes:
    data = bytearray(512)
    data[0:2] = b"MZ"
    data[0x3C:0x40] = struct.pack("<I", 0x80)
    data[0x80:0x84] = b"PE\x00\x00"
    data[0x80 + 22 : 0x80 + 24] = struct.pack("<H", 0x2102 if dll else 0x0102)
    return bytes(data)


def make_zip(members: dict[str, bytes | str], *, stored_first: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if stored_first:
            zf.writestr(zipfile.ZipInfo(stored_first), members[stored_first], compress_type=zipfile.ZIP_STORED)
        for name, content in members.items():
            if name != stored_first:
                zf.writestr(name, content)
    return buf.getvalue()


DOCX = make_zip({"[Content_Types].xml": "<Types/>", "word/document.xml": "<w:document/>"})
XLSX = make_zip({"[Content_Types].xml": "<Types/>", "xl/workbook.xml": "<workbook/>"})
PPTX = make_zip({"[Content_Types].xml": "<Types/>", "ppt/presentation.xml": "<p/>"})
EPUB = make_zip(
    {"mimetype": "application/epub+zip", "META-INF/container.xml": "<container/>"}, stored_first="mimetype"
)
ODT = make_zip({"mimetype": "application/vnd.oasis.opendocument.text", "content.xml": "<x/>"}, stored_first="mimetype")
JAR = make_zip({"META-INF/MANIFEST.MF": "Manifest-Version: 1.0\n", "a/B.class": b"\xca\xfe\xba\xbe\x00\x00\x00\x34"})
APK = make_zip({"AndroidManifest.xml": b"\x03\x00", "classes.dex": b"dex\n035\x00"})
PLAIN_ZIP = make_zip({"notes.txt": "hello", "img/a.png": PNG})


def ole(stream_name: str) -> bytes:
    return b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 500 + stream_name.encode("utf-16-le") + b"\x00" * 100


SAMPLES: dict[str, bytes] = {
    "png": PNG,
    "jpg": JPEG,
    "gif": GIF,
    "pdf": PDF,
    "webp": WEBP,
    "wav": WAV,
    "mp4": MP4,
    "heic": HEIC,
    "mov": MOV,
    "m4a": M4A,
    "mkv": MKV,
    "webm": WEBM,
    "mp3": MP3,
    "flac": FLAC,
    "opus": OGG_OPUS,
    "gz": GZIP,
    "7z": SEVENZ,
    "sqlite": SQLITE,
    "docx": DOCX,
    "xlsx": XLSX,
    "pptx": PPTX,
    "epub": EPUB,
    "odt": ODT,
    "jar": JAR,
    "apk": APK,
    "zip": PLAIN_ZIP,
    "exe": pe_exe(),
    "dll": pe_exe(dll=True),
    "doc": ole("WordDocument"),
    "xls": ole("Workbook"),
    "ppt": ole("PowerPoint Document"),
}

TEXT_SAMPLES: dict[str, str] = {
    "json": json.dumps({"a": 1, "b": [1, 2, 3]}),
    "svg": '<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"></svg>',
    "html": "<!DOCTYPE html>\n<html><head><title>x</title></head><body></body></html>",
    "xml": '<?xml version="1.0"?>\n<root><item/></root>',
    "csv": "name,age,city\nalice,30,paris\nbob,25,rome\ncarol,41,oslo\n",
    "vtt": "WEBVTT\n\n00:00.000 --> 00:01.000\nhello\n",
    "srt": "1\n00:00:01,000 --> 00:00:02,000\nhello\n",
    "vcf": "BEGIN:VCARD\nVERSION:3.0\nFN:Test\nEND:VCARD\n",
    "ics": "BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n",
    "ipynb": json.dumps({"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}),
    "geojson": json.dumps({"type": "FeatureCollection", "features": []}),
    "jsonl": '{"a": 1}\n{"a": 2}\n{"a": 3}\n',
}


@pytest.fixture
def make_file(tmp_path: Path):
    def _make(name: str, content: bytes | str, *, directory: Path | None = None, mtime: float | None = None) -> Path:
        path = (directory or tmp_path) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_bytes(content)
        if mtime is not None:
            import os

            os.utime(path, (mtime, mtime))
        return path

    return _make
