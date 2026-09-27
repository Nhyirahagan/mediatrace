"""Declarative sorting rules."""

from __future__ import annotations

import fnmatch
import json
import re
import tomllib
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..detection import Category, DetectionResult
from ..exceptions import RuleError, TemplateError
from ..naming import parse_size, render_template, split_name, validate_template


class Action(StrEnum):
    MOVE = "move"
    COPY = "copy"
    SKIP = "skip"


#: Fields available in ``destination`` and ``rename`` templates.
RULE_FIELDS = frozenset(
    {
        "category",  # folder name, e.g. "Images"
        "category_id",  # raw value, e.g. "image"
        "ext",  # extension, e.g. "jpg" ("no_ext" when missing)
        "EXT",  # upper-case extension
        "mime_type",  # e.g. "image"
        "mime_subtype",  # e.g. "jpeg"
        "year",
        "month",  # "01".."12"
        "month_name",  # "January"
        "day",
        "date",  # "2024-05-31"
        "quarter",  # "Q2"
        "size_bucket",  # "Small" / "Medium" / "Large" / "Huge"
        "parent",  # name of the directory the file currently lives in
        "stem",  # file name without extension
        "name",  # full file name
        "initial",  # first letter of the name, upper-cased ("#" for non-letters)
    }
)


@dataclass(slots=True)
class FileInfo:
    """A scanned file plus everything rules can match on."""

    path: Path
    size: int
    modified: datetime
    created: datetime
    detection: DetectionResult
    date: datetime  # resolved according to the organizer's ``date_source``

    @property
    def category(self) -> Category:
        return self.detection.category

    @property
    def extension(self) -> str:
        return self.detection.declared_extension or self.detection.extension

    def template_values(self) -> dict[str, object]:
        mime_type, _, mime_subtype = self.detection.mime_type.partition("/")
        stem, _ = split_name(self.path.name)
        ext = self.extension or "no_ext"
        first = stem[:1].upper()
        return {
            "category": self.category.folder,
            "category_id": self.category.value,
            "ext": ext,
            "EXT": ext.upper(),
            "mime_type": mime_type,
            "mime_subtype": mime_subtype,
            "year": f"{self.date.year:04d}",
            "month": f"{self.date.month:02d}",
            "month_name": self.date.strftime("%B"),
            "day": f"{self.date.day:02d}",
            "date": self.date.strftime("%Y-%m-%d"),
            "quarter": f"Q{(self.date.month - 1) // 3 + 1}",
            "size_bucket": size_bucket(self.size),
            "parent": self.path.parent.name,
            "stem": stem,
            "name": self.path.name,
            "initial": first if first.isalpha() else "#",
        }


def size_bucket(size: int) -> str:
    if size < 1024**2:
        return "Small"
    if size < 100 * 1024**2:
        return "Medium"
    if size < 1024**3:
        return "Large"
    return "Huge"


_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhdwy]|mo)?\s*$", re.IGNORECASE)
_DURATION_UNITS = {"s": 1 / 86400, "m": 1 / 1440, "h": 1 / 24, "d": 1, "w": 7, "mo": 30, "y": 365}


def parse_age(value: float | int | str | timedelta | None) -> timedelta | None:
    """Parse an age: ``30`` (days), ``"12h"``, ``"2w"``, ``"6mo"``, ``"1y"`` or a timedelta."""
    if value is None or isinstance(value, timedelta):
        return value
    if isinstance(value, (int, float)):
        return timedelta(days=value)
    m = _DURATION.match(value)
    if not m:
        raise RuleError(f"invalid age: {value!r} (use e.g. 30, '12h', '2w', '6mo', '1y')")
    unit = (m.group(2) or "d").lower()
    return timedelta(days=float(m.group(1)) * _DURATION_UNITS[unit])


def _as_set(value: Collection[str] | str | None) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, str):
        value = [v for v in re.split(r"[,\s]+", value) if v]
    return frozenset(value)


@dataclass
class Rule:
    """Where files that match certain conditions should go.

    All conditions are combined with AND; an empty condition matches anything.
    ``destination`` is a path template relative to the organizer's target root,
    e.g. ``"{category}/{year}/{month}"``. See :data:`RULE_FIELDS`.
    """

    destination: str
    name: str = ""
    categories: Collection[Category | str] = ()
    extensions: Collection[str] = ()
    mime_types: Collection[str] = ()
    name_pattern: str | None = None
    glob: str | None = None
    min_size: int | str | None = None
    max_size: int | str | None = None
    older_than: float | str | timedelta | None = None
    newer_than: float | str | timedelta | None = None
    predicate: Callable[[FileInfo], bool] | None = None
    action: Action | str = Action.MOVE
    rename: str | None = None
    priority: int = 0
    _regex: re.Pattern[str] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            self.categories = frozenset(Category.parse(c) for c in _as_set(self.categories))  # type: ignore[arg-type]
        except ValueError as exc:
            raise RuleError(str(exc)) from exc
        self.extensions = frozenset(e.lower().lstrip(".") for e in _as_set(self.extensions))  # type: ignore[arg-type]
        self.mime_types = tuple(m.lower() for m in _as_set(self.mime_types))  # type: ignore[arg-type]
        try:
            self.action = Action(str(self.action).lower())
        except ValueError as exc:
            raise RuleError(f"invalid action {self.action!r}; use move, copy or skip") from exc
        try:
            self.min_size = parse_size(self.min_size)
            self.max_size = parse_size(self.max_size)
        except ValueError as exc:
            raise RuleError(str(exc)) from exc
        self.older_than = parse_age(self.older_than)
        self.newer_than = parse_age(self.newer_than)
        if self.name_pattern:
            try:
                self._regex = re.compile(self.name_pattern, re.IGNORECASE)
            except re.error as exc:
                raise RuleError(f"invalid name_pattern {self.name_pattern!r}: {exc}") from exc
        try:
            if self.action is not Action.SKIP:
                validate_template(self.destination, RULE_FIELDS)
            if self.rename:
                if "/" in self.rename or "\\" in self.rename:
                    raise RuleError("rename templates cannot contain path separators")
                validate_template(self.rename, RULE_FIELDS)
        except TemplateError as exc:
            raise RuleError(str(exc)) from exc
        if not self.name:
            self.name = self.destination if self.action is not Action.SKIP else "skip"

    # -- matching ------------------------------------------------------------

    def matches(self, info: FileInfo, now: datetime | None = None) -> bool:
        if self.categories and info.category not in self.categories:
            return False
        if self.extensions and not (
            info.detection.declared_extension in self.extensions or info.detection.extension in self.extensions
        ):
            return False
        if self.mime_types and not any(fnmatch.fnmatch(info.detection.mime_type, p) for p in self.mime_types):
            return False
        if self._regex and not self._regex.search(info.path.name):
            return False
        if self.glob and not fnmatch.fnmatch(info.path.name.lower(), self.glob.lower()):
            return False
        if self.min_size is not None and info.size < self.min_size:  # type: ignore[operator]
            return False
        if self.max_size is not None and info.size > self.max_size:  # type: ignore[operator]
            return False
        if self.older_than is not None or self.newer_than is not None:
            age = (now or datetime.now()) - info.modified
            if self.older_than is not None and age < self.older_than:  # type: ignore[operator]
                return False
            if self.newer_than is not None and age > self.newer_than:  # type: ignore[operator]
                return False
        if self.predicate is not None and not self.predicate(info):
            return False
        return True

    # -- rendering -------------------------------------------------------------

    def render_destination(self, info: FileInfo) -> str:
        return render_template(self.destination, info.template_values())

    def render_name(self, info: FileInfo, *, fix_extension: bool = False) -> str:
        stem, ext = split_name(info.path.name)
        if fix_extension and info.detection.extension_mismatch and info.detection.extension:
            ext = "." + info.detection.extension
        if self.rename:
            stem = render_template(self.rename, info.template_values())
        return stem + ext

    # -- serialization ----------------------------------------------------------

    _ALIASES = {
        "category": "categories",
        "extension": "extensions",
        "ext": "extensions",
        "exts": "extensions",
        "mime": "mime_types",
        "mime_type": "mime_types",
        "pattern": "name_pattern",
        "regex": "name_pattern",
        "dest": "destination",
        "to": "destination",
    }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Rule:
        kwargs: dict[str, Any] = {}
        for key, value in data.items():
            key = cls._ALIASES.get(key, key)
            if key in ("predicate", "_regex"):
                raise RuleError(f"{key!r} cannot be set from configuration")
            kwargs[key] = value
        if "destination" not in kwargs:
            if str(kwargs.get("action", "")).lower() == "skip":
                kwargs["destination"] = ""
            else:
                raise RuleError(f"rule {data!r} has no destination")
        try:
            return cls(**kwargs)
        except TypeError as exc:
            raise RuleError(f"invalid rule {data!r}: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name, "destination": self.destination, "action": str(self.action)}
        if self.categories:
            data["categories"] = sorted(str(c) for c in self.categories)
        if self.extensions:
            data["extensions"] = sorted(self.extensions)
        if self.mime_types:
            data["mime_types"] = list(self.mime_types)
        for key in ("name_pattern", "glob", "min_size", "max_size", "rename"):
            if getattr(self, key) is not None:
                data[key] = getattr(self, key)
        for key in ("older_than", "newer_than"):
            value = getattr(self, key)
            if value is not None:
                data[key] = value.total_seconds() / 86400
        if self.priority:
            data["priority"] = self.priority
        return data


def load_rules(source: str | Path | dict | list) -> list[Rule]:
    """Load rules from a JSON/TOML file, or from already-parsed data.

    Accepted shapes: ``{"rules": [...]}`` or a bare list of rule tables. TOML
    files use ``[[rules]]`` arrays of tables.
    """
    if isinstance(source, (str, Path)):
        path = Path(source)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuleError(f"cannot read rules file {path}: {exc}") from exc
        try:
            data: Any = tomllib.loads(text) if path.suffix.lower() == ".toml" else json.loads(text)
        except (tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
            raise RuleError(f"cannot parse rules file {path}: {exc}") from exc
    else:
        data = source
    items = data.get("rules") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise RuleError("rules must be a list (or a mapping with a 'rules' list)")
    return [item if isinstance(item, Rule) else Rule.from_dict(item) for item in items]


# --------------------------------------------------------------------------- #
# Presets
# --------------------------------------------------------------------------- #


def _preset_category() -> list[Rule]:
    return [Rule("{category}", name="by-category")]


def _preset_category_date() -> list[Rule]:
    return [Rule("{category}/{year}/{month}", name="by-category-and-date")]


def _preset_date() -> list[Rule]:
    return [Rule("{year}/{month}", name="by-date")]


def _preset_extension() -> list[Rule]:
    return [Rule("{EXT}", name="by-extension")]


def _preset_media_timeline() -> list[Rule]:
    return [
        Rule("Photos/{year}/{month}", name="photos", categories=[Category.IMAGE], priority=10),
        Rule("Videos/{year}/{month}", name="videos", categories=[Category.VIDEO], priority=10),
        Rule("{category}", name="everything-else"),
    ]


def _preset_downloads() -> list[Rule]:
    """Typical Downloads-folder cleanup: installers and archives apart, big files flagged."""
    return [
        Rule("Installers", name="installers", categories=[Category.EXECUTABLE, Category.DISK_IMAGE], priority=20),
        Rule("Archives", name="archives", categories=[Category.ARCHIVE], priority=20),
        Rule("Screenshots/{year}-{month}", name="screenshots", categories=[Category.IMAGE],
             name_pattern=r"^(screenshot|screen shot|scr_|capture)", priority=15),
        Rule("{category}", name="by-category"),
    ]


PRESETS: dict[str, Callable[[], list[Rule]]] = {
    "category": _preset_category,
    "category-date": _preset_category_date,
    "date": _preset_date,
    "extension": _preset_extension,
    "media-timeline": _preset_media_timeline,
    "downloads": _preset_downloads,
}


def preset(name: str) -> list[Rule]:
    """Return a fresh copy of a named rule preset (see :data:`PRESETS`)."""
    try:
        return PRESETS[name]()
    except KeyError:
        raise RuleError(f"unknown preset {name!r}; available: {', '.join(PRESETS)}") from None


def default_rules() -> list[Rule]:
    return _preset_category()


def sort_rules(rules: Iterable[Rule]) -> list[Rule]:
    """Highest priority first; declaration order breaks ties."""
    return sorted(rules, key=lambda r: -r.priority)
