"""Image and audio/video metadata helpers backed by optional tools (Pillow, ffmpeg)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_EXIF_IFD = 0x8769
_DATETIME_ORIGINAL = 36867
_DATETIME_DIGITIZED = 36868
_DATETIME = 306
_SAVE_FORMATS = {"JPEG", "PNG", "WEBP", "TIFF", "BMP", "GIF"}


def pillow_available() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def ffmpeg_path() -> str | None:
    return os.environ.get("MEDIATRACE_FFMPEG") or shutil.which("ffmpeg")


def exif_datetime(path: str | os.PathLike[str]) -> datetime | None:
    """Capture date from EXIF (DateTimeOriginal), or None if unavailable."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            sub = exif.get_ifd(_EXIF_IFD)
            raw = sub.get(_DATETIME_ORIGINAL) or sub.get(_DATETIME_DIGITIZED) or exif.get(_DATETIME)
    except Exception:  # Pillow raises many exception types for odd files
        return None
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw).strip("\x00 ")[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def strip_image_metadata(path: str | os.PathLike[str], *, quality: int = 95) -> bool:
    """Remove EXIF/XMP/GPS/comments from an image in place.

    The EXIF orientation is applied to the pixels first so the image still
    displays upright. The ICC colour profile is kept. Returns True if the file
    was rewritten.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return False
    path = Path(path)
    try:
        with Image.open(path) as im:
            fmt = (im.format or "").upper()
            if fmt == "MPO":
                fmt = "JPEG"
            if fmt not in _SAVE_FORMATS or getattr(im, "n_frames", 1) > 1:
                return False
            im.load()
            icc = im.info.get("icc_profile")
            transparency = im.info.get("transparency")
            clean = ImageOps.exif_transpose(im) or im
            clean = clean.copy()
            clean.info = {}
    except Exception as exc:
        log.debug("cannot open %s for metadata stripping: %s", path, exc)
        return False

    save_kwargs: dict[str, object] = {}
    if icc:
        save_kwargs["icc_profile"] = icc
    if fmt == "JPEG":
        if clean.mode not in ("RGB", "L", "CMYK"):
            clean = clean.convert("RGB")
        save_kwargs.update(quality=quality, optimize=True)
    elif fmt == "PNG":
        if transparency is not None:
            save_kwargs["transparency"] = transparency
        save_kwargs["optimize"] = True
    elif fmt == "WEBP":
        save_kwargs.update(quality=quality)
    elif fmt == "GIF" and transparency is not None:
        save_kwargs["transparency"] = transparency

    tmp = path.with_name(f".{path.name}.mediatrace-tmp")
    try:
        clean.save(tmp, format=fmt, **save_kwargs)
        os.replace(tmp, path)
    except Exception as exc:
        log.debug("cannot rewrite %s: %s", path, exc)
        tmp.unlink(missing_ok=True)
        return False
    return True


def convert_image(path: str | os.PathLike[str], target_format: str, *, quality: int = 92) -> Path | None:
    """Convert an image to ``jpg``/``png``/``webp``; returns the new path (old file removed)."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    path = Path(path)
    target_format = target_format.lower().lstrip(".")
    fmt = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}.get(target_format)
    if fmt is None:
        raise ValueError(f"unsupported image format: {target_format!r}")
    out = path.with_suffix("." + ("jpg" if fmt == "JPEG" else target_format))
    try:
        with Image.open(path) as im:
            im.load()
            icc = im.info.get("icc_profile")
            img = ImageOps.exif_transpose(im) or im
            if fmt == "JPEG" and img.mode not in ("RGB", "L"):
                img = img.convert("RGBA").convert("RGB") if "A" in img.mode or img.mode == "P" else img.convert("RGB")
            kwargs: dict[str, object] = {"icc_profile": icc} if icc else {}
            if fmt in ("JPEG", "WEBP"):
                kwargs["quality"] = quality
            tmp = out.with_name(f".{out.name}.mediatrace-tmp")
            img.save(tmp, format=fmt, **kwargs)
    except Exception as exc:
        log.debug("cannot convert %s: %s", path, exc)
        return None
    os.replace(tmp, out)
    if out != path:
        path.unlink(missing_ok=True)
    return out


def strip_av_metadata(path: str | os.PathLike[str], *, timeout: float = 600) -> bool:
    """Remove container metadata (titles, encoder, GPS, chapters) from audio/video via ffmpeg.

    Streams are copied, not re-encoded. Returns True if the file was rewritten.
    """
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        return False
    path = Path(path)
    tmp = path.with_name(f".{path.stem}.mediatrace-tmp{path.suffix}")
    cmd = [ffmpeg, "-y", "-v", "error", "-i", str(path), "-map", "0:v?", "-map", "0:a?"]
    if path.suffix.lower() in (".mkv", ".mka", ".webm"):
        cmd += ["-map", "0:s?"]
    cmd += ["-map_metadata", "-1", "-map_chapters", "-1", "-c", "copy", "-fflags", "+bitexact"]
    if path.suffix.lower() in (".mp4", ".m4a", ".m4v", ".mov"):
        cmd += ["-movflags", "+faststart"]
    cmd.append(str(tmp))
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("ffmpeg failed on %s: %s", path, exc)
        tmp.unlink(missing_ok=True)
        return False
    if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        log.debug("ffmpeg failed on %s: %s", path, proc.stderr.decode(errors="replace").strip())
        tmp.unlink(missing_ok=True)
        return False
    os.replace(tmp, path)
    return True
