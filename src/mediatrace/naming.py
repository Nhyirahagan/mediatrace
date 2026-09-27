"""Cross-platform file name sanitizing and safe path templates."""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from collections.abc import Mapping
from pathlib import Path

from .exceptions import TemplateError

_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
)
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_WHITESPACE = re.compile(r"\s+")
_FIELD = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::([^{}]*))?\}")
_COMPOUND = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tar.lz4")


def split_name(name: str) -> tuple[str, str]:
    """Split ``"a.tar.gz"`` into ``("a", ".tar.gz")`` and ``"a.jpg"`` into ``("a", ".jpg")``."""
    low = name.lower()
    for compound in _COMPOUND:
        if low.endswith(compound) and len(name) > len(compound):
            return name[: -len(compound)], name[-len(compound) :]
    stem, ext = os.path.splitext(name)
    if not stem:  # dotfiles like ".bashrc"
        return name, ""
    return stem, ext


def sanitize_filename(
    name: str,
    *,
    replacement: str = "_",
    max_length: int = 180,
    ascii_only: bool = False,
    fallback: str = "untitled",
) -> str:
    """Make ``name`` safe to use as a single path component on Windows, macOS and Linux.

    Removes path separators, reserved and control characters (including invisible
    bidi/zero-width characters), collapses whitespace, avoids Windows reserved
    device names and truncates to ``max_length`` characters while keeping the extension.
    """
    name = str(name)
    if ascii_only:
        name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    else:
        name = unicodedata.normalize("NFC", name)
    name = _INVALID_CHARS.sub(replacement, name)
    name = "".join(ch for ch in name if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co"))
    name = _WHITESPACE.sub(" ", name).strip(" .")
    if replacement:
        name = re.sub(f"(?:{re.escape(replacement)}){{2,}}", replacement, name)
    if not name or not name.strip(replacement + " ."):
        name = fallback
    if name.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        name = f"{replacement}{name}"
    if len(name) > max_length:
        stem, ext = split_name(name)
        if len(ext) > 16 or len(ext) >= max_length:
            stem, ext = name, ""
        name = stem[: max_length - len(ext)].rstrip(" .") + ext
    return name


def template_fields(template: str) -> set[str]:
    """Field names referenced by a template such as ``"{category}/{year}"``."""
    return {m.group(1) for m in _FIELD.finditer(template)}


def validate_template(template: str, allowed: set[str] | frozenset[str]) -> None:
    unknown = template_fields(template) - set(allowed)
    if unknown:
        raise TemplateError(
            f"unknown template field(s) {', '.join(sorted(unknown))} in {template!r}; "
            f"available: {', '.join(sorted(allowed))}"
        )
    for segment in template.replace("\\", "/").split("/"):
        if segment.strip() == "..":
            raise TemplateError(f"'..' is not allowed in templates: {template!r}")


def render_template(
    template: str,
    values: Mapping[str, object],
    *,
    missing: str = "unknown",
    max_segment_length: int = 150,
    ascii_only: bool = False,
) -> str:
    """Render a ``/``-separated path template into a safe relative path.

    Every rendered segment is sanitized, so field values can never introduce
    extra directories, absolute paths or ``..`` traversal. Format specs are
    supported: ``{index:03d}``, ``{title:.60}``.
    """
    segments: list[str] = []
    for raw in template.replace("\\", "/").split("/"):
        if not raw.strip() or raw.strip() == ".":
            continue
        if raw.strip() == "..":
            raise TemplateError(f"'..' is not allowed in templates: {template!r}")

        def _sub(m: re.Match[str]) -> str:
            key, spec = m.group(1), m.group(2)
            if key not in values:
                raise TemplateError(f"unknown template field {{{key}}}; available: {', '.join(sorted(values))}")
            value = values[key]
            if value is None or value == "":
                return missing
            try:
                text = format(value, spec) if spec else str(value)
            except (ValueError, TypeError) as exc:
                raise TemplateError(f"bad format spec {{{key}:{spec}}}: {exc}") from exc
            return text.replace("/", "-").replace("\\", "-")

        rendered = _FIELD.sub(_sub, raw)
        segments.append(
            sanitize_filename(rendered, max_length=max_segment_length, ascii_only=ascii_only, fallback=missing)
        )
    return "/".join(segments)


def path_key(path: Path) -> str:
    """Comparison key that respects the platform's case sensitivity."""
    return os.path.normcase(os.path.abspath(path))


def unique_path(path: Path, reserved: set[str] | None = None) -> Path:
    """Return ``path`` or the first free ``"name (n).ext"`` variant.

    ``reserved`` holds :func:`path_key` values already claimed by a plan but not
    yet written to disk.
    """
    reserved = reserved if reserved is not None else set()
    if not path.exists() and path_key(path) not in reserved:
        return path
    stem, ext = split_name(path.name)
    n = 1
    while True:
        candidate = path.with_name(f"{stem} ({n}){ext}")
        if not candidate.exists() and path_key(candidate) not in reserved:
            return candidate
        n += 1


def file_digest(path: str | os.PathLike[str], algorithm: str = "sha256") -> str:
    """Hex digest of a file's content, streamed from disk."""
    with open(path, "rb") as fh:
        return hashlib.file_digest(fh, algorithm).hexdigest()


def partial_digest(path: str | os.PathLike[str], size: int = 65536) -> str:
    """Cheap digest over the first and last ``size`` bytes, used to pre-filter duplicates."""
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as fh:
        h.update(fh.read(size))
        fh.seek(0, os.SEEK_END)
        end = fh.tell()
        if end > size * 2:
            fh.seek(end - size)
            h.update(fh.read(size))
    return h.hexdigest()


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024 or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)(i?b?)\s*$", re.IGNORECASE)


def parse_size(value: int | float | str | None) -> int | None:
    """Parse ``"10MB"``, ``"1.5 GiB"``, ``"500k"`` or a plain number of bytes (1 KB = 1024 B)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = _SIZE.match(value)
    if not m:
        raise ValueError(f"invalid size: {value!r}")
    power = " kmgt".index(m.group(2).lower() or " ")
    return int(float(m.group(1)) * 1024**power)
