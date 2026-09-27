"""Heuristics for text-based formats (JSON, XML/SVG/HTML, CSV, scripts, subtitles...)."""

from __future__ import annotations

import csv
import json
import re

from .categories import Category
from .signatures import Probe, TypeMatch, match

#: Only parse JSON candidates up to this size.
JSON_PARSE_LIMIT = 4 * 1024 * 1024

_BOMS = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)


def decode_text(data: bytes, *, truncated: bool = False) -> str | None:
    """Decode bytes that look like text, or return None for binary data.

    ``truncated`` means ``data`` is a prefix of a larger file, so a multi-byte
    character may be cut off at the end.
    """
    if not data:
        return ""
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            try:
                return data.decode(encoding, errors="ignore" if truncated else "strict")
            except UnicodeDecodeError:
                return None
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        if truncated and exc.start >= len(data) - 3:
            try:
                return data[: exc.start].decode("utf-8")
            except UnicodeDecodeError:
                return None
    # Legacy 8-bit encodings: accept when control characters are rare.
    controls = sum(1 for b in data if b < 32 and b not in (9, 10, 12, 13, 27))
    if controls / len(data) > 0.01:
        return None
    return data.decode("cp1252", errors="replace")


_INTERPRETERS: list[tuple[tuple[str, ...], str]] = [
    (("python", "python2", "python3", "pypy", "pypy3"), "py"),
    (("sh", "bash", "zsh", "dash", "ksh", "ash", "fish"), "sh"),
    (("node", "nodejs", "deno", "bun"), "js"),
    (("perl",), "pl"),
    (("ruby",), "rb"),
    (("php",), "php"),
    (("lua", "luajit"), "lua"),
    (("rscript",), "r"),
    (("pwsh", "powershell"), "ps1"),
    (("julia",), "jl"),
    (("elixir",), "exs"),
]


def _shebang(first_line: str) -> TypeMatch:
    parts = first_line[2:].strip().split()
    interpreter = ""
    if parts:
        interpreter = parts[0].rsplit("/", 1)[-1]
        if interpreter == "env":
            rest = [p for p in parts[1:] if not p.startswith("-") and "=" not in p]
            interpreter = rest[0] if rest else ""
    name = re.sub(r"[\d.]+$", "", interpreter.lower())
    for names, ext in _INTERPRETERS:
        if name in names or interpreter.lower() in names:
            return match(ext, f"{interpreter} script", 0.9, method="text")
    return match("sh", f"Script ({interpreter or 'unknown interpreter'})", 0.7, method="text")


_SRT = re.compile(r"\d+\s*\r?\n\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->")
_HEADER_LINE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:\s")
_EMAIL_KEYS = ("return-path:", "received:", "delivered-to:", "message-id:", "mime-version:", "from:", "subject:")
_HTML = re.compile(r"<(html|head|body)[\s>]")


def _json(text: str, probe: Probe) -> TypeMatch | None:
    if probe.size > JSON_PARSE_LIMIT:
        # Too large to parse cheaply; fall back to structural hints.
        if re.match(r'\s*[\[{]\s*("|\{|\[|\d|-|true|false|null)', text):
            return match("json", "JSON data (large, unparsed)", 0.6, method="text")
        return None
    full = decode_text(probe.read_at(0, probe.size))
    if full is None:
        return None
    full = full.lstrip("﻿")
    try:
        obj = json.loads(full)
    except ValueError:
        lines = [ln for ln in full.splitlines() if ln.strip()]
        if len(lines) >= 2:
            try:
                for ln in lines[:50]:
                    json.loads(ln)
            except ValueError:
                return None
            return match("jsonl", "JSON Lines data", 0.9, method="text")
        return None
    if isinstance(obj, dict):
        if "nbformat" in obj and "cells" in obj:
            return match("ipynb", "Jupyter notebook", 0.97, method="text")
        if obj.get("type") in ("FeatureCollection", "Feature"):
            return match("geojson", "GeoJSON data", 0.95, method="text")
        if "asset" in obj and isinstance(obj.get("asset"), dict) and "version" in obj["asset"]:
            return match("gltf", "glTF model", 0.9, method="text")
    return match("json", "JSON data", 0.95, method="text")


def _markup(low: str) -> TypeMatch | None:
    if low.startswith("<svg") or (low.startswith("<?xml") and "<svg" in low):
        return match("svg", "SVG vector image", 0.95, method="text")
    if low.startswith("<!doctype html") or low.startswith("<html") or _HTML.search(low[:2048]):
        return match("html", "HTML document", 0.9, method="text")
    xml_like = low.startswith("<?xml")
    for needle, ext, desc in (
        ("<rss", "rss", "RSS feed"),
        ("<feed", "atom", "Atom feed"),
        ("<plist", "plist", "Property list"),
        ("<gpx", "gpx", "GPS exchange data"),
        ("<kml", "kml", "KML geographic data"),
        ("<mxfile", "drawio", "draw.io diagram"),
        ("<fictionbook", "fb2", "FictionBook e-book"),
        ("<collada", "dae", "COLLADA 3D model"),
    ):
        if needle in low and (xml_like or low.startswith(needle)):
            return match(ext, desc, 0.9, method="text")
    if xml_like and "<tt" in low and "ttml" in low:
        return match("ttml", "TTML subtitles", 0.9, method="text")
    if xml_like:
        return match("xml", "XML document", 0.9, method="text")
    return None


def _delimited(text: str) -> TypeMatch | None:
    lines = [ln for ln in text.splitlines()[:40] if ln.strip()]
    if len(lines) < 3:
        return None
    sample = lines[:-1]  # the last line of a prefix may be cut off
    for delimiter, ext in ((",", "csv"), ("\t", "tsv"), (";", "csv"), ("|", "csv")):
        if delimiter not in sample[0]:
            continue
        try:
            counts = [len(row) for row in csv.reader(sample, delimiter=delimiter)]
        except csv.Error:
            continue
        if counts and counts[0] >= 2 and all(c == counts[0] for c in counts):
            label = "Tab-separated values" if ext == "tsv" else "Comma-separated values"
            return match(ext, label, 0.75, method="text")
    return None


def sniff_text(text: str, probe: Probe) -> TypeMatch:
    """Classify decoded text. Always returns a match (plain text as a fallback)."""
    s = text.lstrip("﻿ \t\r\n")
    low = s[:4096].lower()

    if s.startswith("#!"):
        return _shebang(s.split("\n", 1)[0])
    if s.startswith("<"):
        found = _markup(low)
        if found:
            return found
    if s[:1] in "{[":
        found = _json(s, probe)
        if found:
            return found
    if s.startswith("BEGIN:VCARD"):
        return match("vcf", "vCard contact", 0.97, method="text")
    if s.startswith("BEGIN:VCALENDAR"):
        return match("ics", "iCalendar data", 0.97, method="text")
    if s.startswith("WEBVTT"):
        return match("vtt", "WebVTT subtitles", 0.97, method="text")
    if _SRT.match(s):
        return match("srt", "SubRip subtitles", 0.95, method="text")
    if s.startswith("[Script Info]"):
        return match("ass", "Advanced SubStation subtitles", 0.95, method="text")
    if s.startswith("-----BEGIN "):
        return match("pem", "PEM-encoded key or certificate", 0.95, method="text")
    if low.startswith("solid ") and "facet normal" in low:
        return match("stl", "ASCII STL model", 0.9, method="text")
    if low.startswith("ply\n") or low.startswith("ply\r\n"):
        return match("ply", "PLY model", 0.9, method="text")

    head_lines = s.splitlines()[:12]
    header_like = [ln for ln in head_lines if _HEADER_LINE.match(ln)]
    if len(header_like) >= 3 and any(ln.lower().startswith(_EMAIL_KEYS) for ln in header_like):
        return match("eml", "Email message", 0.85, method="text")

    found = _delimited(s)
    if found:
        return found
    return TypeMatch("text/plain", "txt", Category.TEXT, "Plain text", 0.5, "text")
