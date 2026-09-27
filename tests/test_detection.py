from __future__ import annotations

import pytest

from mediatrace import Category, FileTypeDetector, detect
from mediatrace.detection import declared_extension
from mediatrace.exceptions import DetectionError

from .conftest import ELF, JPEG, MP4, PNG, SAMPLES, TEXT_SAMPLES, WEBP


@pytest.fixture
def detector() -> FileTypeDetector:
    return FileTypeDetector()


@pytest.mark.parametrize("ext", sorted(SAMPLES))
def test_binary_signatures_without_extension(detector, ext):
    result = detector.detect_bytes(SAMPLES[ext])
    assert result.extension == ext, result
    assert not result.extension_mismatch


@pytest.mark.parametrize("ext", sorted(TEXT_SAMPLES))
def test_text_formats_without_extension(detector, ext):
    result = detector.detect_bytes(TEXT_SAMPLES[ext].encode())
    assert result.extension == ext, result


@pytest.mark.parametrize(
    ("ext", "category"),
    [
        ("png", Category.IMAGE),
        ("heic", Category.IMAGE),
        ("mp4", Category.VIDEO),
        ("m4a", Category.AUDIO),
        ("opus", Category.AUDIO),
        ("docx", Category.DOCUMENT),
        ("xlsx", Category.SPREADSHEET),
        ("pptx", Category.PRESENTATION),
        ("epub", Category.EBOOK),
        ("7z", Category.ARCHIVE),
        ("sqlite", Category.DATABASE),
        ("exe", Category.EXECUTABLE),
    ],
)
def test_categories(detector, ext, category):
    assert detector.detect_bytes(SAMPLES[ext], f"file.{ext}").category is category


def test_detect_from_path(make_file):
    path = make_file("photo.png", PNG)
    result = detect(path)
    assert result.mime_type == "image/png"
    assert result.path == path
    assert result.size == len(PNG)


def test_mismatch_flags_lying_extension(detector):
    result = detector.detect_bytes(PNG, "holiday.jpg")
    assert result.extension == "png"
    assert result.extension_mismatch
    assert result.declared_extension == "jpg"


def test_suggested_name(make_file, detector):
    path = make_file("image.jpg", WEBP)
    assert detector.detect(path).suggested_name == "image.webp"


def test_html_error_page_saved_as_jpg(detector):
    result = detector.detect_bytes(b"<!DOCTYPE html><html><body>403 Forbidden</body></html>", "cat.jpg")
    assert result.mime_type == "text/html"
    assert result.extension_mismatch


@pytest.mark.parametrize(
    ("data", "name", "expected"),
    [
        (JPEG, "photo.jpeg", "jpeg"),  # alias of detected jpg
        (MP4, "song.m4a", "m4a"),  # same ISO-BMFF container family
        (SAMPLES["zip"], "comic.cbz", "cbz"),  # ZIP-based format
        (SAMPLES["gz"], "backup.tar.gz", "tar.gz"),
        (SAMPLES["docx"], "macro.docm", "docm"),
    ],
)
def test_refinements_keep_declared_extension(detector, data, name, expected):
    result = detector.detect_bytes(data, name)
    assert result.extension == expected
    assert not result.extension_mismatch


def test_textual_extension_wins_for_text(detector):
    code = "import os\nprint(os.getcwd())\n"
    assert detector.detect_bytes(code.encode(), "script.py").mime_type == "text/x-python"
    assert detector.detect_bytes(b"let x: number = 1;\n", "app.ts").category is Category.CODE


def test_ts_extension_binary_is_video(detector):
    packet = b"G" + b"\x00" * 187
    result = detector.detect_bytes(packet * 4, "clip.ts")
    assert result.category is Category.VIDEO


def test_shebang(detector):
    assert detector.detect_bytes(b"#!/usr/bin/env python3\nprint(1)\n").extension == "py"
    assert detector.detect_bytes(b"#!/bin/bash\necho hi\n").extension == "sh"


def test_utf16_text(detector):
    result = detector.detect_bytes("hello world\n".encode("utf-16"))
    assert result.category is Category.TEXT


def test_elf_has_no_canonical_extension(detector):
    result = detector.detect_bytes(ELF, "mytool")
    assert result.category is Category.EXECUTABLE
    assert not result.extension_mismatch


def test_empty_file_uses_extension(detector, make_file):
    result = detector.detect(make_file("empty.mp3", b""))
    assert result.category is Category.AUDIO
    assert result.method == "extension"


def test_unknown_binary_falls_back_to_extension(detector):
    result = detector.detect_bytes(bytes(range(256)) * 4, "track.flac")
    assert result.method == "extension"
    assert result.category is Category.AUDIO


def test_unknown_binary_without_extension(detector):
    result = detector.detect_bytes(bytes(range(256)) * 4)
    assert result.category is Category.OTHER
    assert result.mime_type == "application/octet-stream"


def test_custom_signature(detector):
    detector.register_signature(b"MYFMT1", "myf", "application/x-myformat", "data", description="My format")
    result = detector.detect_bytes(b"MYFMT1" + b"\x00" * 10)
    assert result.extension == "myf"
    assert result.category is Category.DATA


def test_directory_raises(detector, tmp_path):
    with pytest.raises(DetectionError):
        detector.detect(tmp_path)


def test_missing_file_raises(detector, tmp_path):
    with pytest.raises(DetectionError):
        detector.detect(tmp_path / "nope.bin")


def test_detect_many(detector, make_file, tmp_path):
    a = make_file("a.png", PNG)
    b = make_file("b.pdf", SAMPLES["pdf"])
    results = detector.detect_many([a, b, tmp_path / "missing"])
    assert set(results) == {a, b}


@pytest.mark.parametrize(
    ("name", "expected"),
    [("a.JPG", "jpg"), ("archive.tar.gz", "tar.gz"), (".bashrc", ""), ("noext", ""), ("a.b.c", "c")],
)
def test_declared_extension(name, expected):
    assert declared_extension(name) == expected


def test_category_parse():
    assert Category.parse("images") is Category.IMAGE
    assert Category.parse("Disk Images") is Category.DISK_IMAGE
    assert Category.parse(Category.VIDEO) is Category.VIDEO
    with pytest.raises(ValueError):
        Category.parse("nonsense")
