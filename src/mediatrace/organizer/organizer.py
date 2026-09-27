"""Plan-then-execute directory organizer with dedupe, conflict handling and undo."""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import shutil
import stat
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from ..detection import DetectionResult, FileTypeDetector
from ..exceptions import DetectionError, JournalError, OrganizerError
from ..imaging import exif_datetime
from ..naming import file_digest, partial_digest, path_key, unique_path
from .rules import Action, FileInfo, Rule, default_rules, sort_rules

log = logging.getLogger(__name__)

ConflictStrategy = Literal["rename", "skip", "overwrite"]
DateSource = Literal["modified", "created", "exif"]

JOURNAL_DIR = ".mediatrace"
JOURNAL_VERSION = 1

#: Name patterns never touched by default: OS litter and in-progress downloads.
DEFAULT_IGNORE: tuple[str, ...] = (
    JOURNAL_DIR,
    "desktop.ini",
    "thumbs.db",
    ".ds_store",
    "*.part",
    "*.partial",
    "*.crdownload",
    "*.download",
    "*.tmp",
    "*.ytdl",
    "~$*",
    ".~lock.*",
    "*.mediatrace-tmp*",
)


class OpStatus(StrEnum):
    PLANNED = "planned"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(slots=True)
class Operation:
    source: Path
    destination: Path | None
    action: Action
    rule: str | None = None
    reason: str = ""
    detection: DetectionResult | None = None
    size: int = 0
    status: OpStatus = OpStatus.PLANNED
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.source),
            "destination": str(self.destination) if self.destination else None,
            "action": str(self.action),
            "rule": self.rule,
            "reason": self.reason,
            "status": str(self.status),
            "error": self.error,
            "size": self.size,
            "category": str(self.detection.category) if self.detection else None,
            "mime_type": self.detection.mime_type if self.detection else None,
        }


@dataclass
class OrganizePlan:
    source_root: Path
    target_root: Path
    operations: list[Operation] = field(default_factory=list)

    def __iter__(self) -> Iterator[Operation]:
        return iter(self.operations)

    def __len__(self) -> int:
        return len(self.operations)

    @property
    def actionable(self) -> list[Operation]:
        return [op for op in self.operations if op.action is not Action.SKIP]

    def summary(self) -> dict[str, Any]:
        by_action = Counter(str(op.action) for op in self.operations)
        by_category = Counter(
            str(op.detection.category) for op in self.actionable if op.detection is not None
        )
        return {
            "files": len(self.operations),
            "actions": dict(by_action),
            "categories": dict(by_category.most_common()),
            "bytes": sum(op.size for op in self.actionable),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_root": str(self.source_root),
            "target_root": str(self.target_root),
            "summary": self.summary(),
            "operations": [op.to_dict() for op in self.operations],
        }


@dataclass
class OrganizeReport:
    plan: OrganizePlan
    dry_run: bool = False
    journal_path: Path | None = None
    elapsed: float = 0.0
    removed_dirs: list[Path] = field(default_factory=list)

    @property
    def operations(self) -> list[Operation]:
        return self.plan.operations

    def _with(self, status: OpStatus) -> list[Operation]:
        return [op for op in self.operations if op.status is status]

    @property
    def done(self) -> list[Operation]:
        return self._with(OpStatus.DONE)

    @property
    def skipped(self) -> list[Operation]:
        return self._with(OpStatus.SKIPPED)

    @property
    def failed(self) -> list[Operation]:
        return self._with(OpStatus.FAILED)

    @property
    def ok(self) -> bool:
        return not self.failed

    def summary(self) -> dict[str, Any]:
        data = self.plan.summary()
        data.update(
            dry_run=self.dry_run,
            statuses=dict(Counter(str(op.status) for op in self.operations)),
            journal=str(self.journal_path) if self.journal_path else None,
            elapsed=round(self.elapsed, 3),
        )
        return data

    def to_dict(self) -> dict[str, Any]:
        data = self.plan.to_dict()
        data["summary"] = self.summary()
        data["removed_dirs"] = [str(p) for p in self.removed_dirs]
        return data

    def __str__(self) -> str:
        s = self.summary()
        verb = "would process" if self.dry_run else "processed"
        return (
            f"{verb} {s['files']} files: "
            + ", ".join(f"{v} {k}" for k, v in sorted(s["statuses"].items()))
            + (f" (journal: {self.journal_path})" if self.journal_path else "")
        )


@dataclass
class UndoReport:
    journal_path: Path
    restored: list[Path] = field(default_factory=list)
    removed_copies: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


ProgressCallback = Callable[[str, int, int], None]


class FileOrganizer:
    """Sort a messy directory into a clean structure using content-based detection.

    Organizing is a two-step process so callers can preview changes:

    >>> org = FileOrganizer("~/Sorted", rules=preset("media-timeline"), dedupe=True)
    >>> plan = org.plan("~/Downloads")      # nothing touched yet
    >>> report = org.execute(plan)          # moves happen here; a journal is written
    >>> FileOrganizer.undo(report.journal_path)

    Args:
        target: Root the rule destinations are relative to. ``None`` organizes in place.
        rules: Ordered rules; the first match (by priority) wins. Defaults to "by category".
        detector: A custom :class:`FileTypeDetector`.
        conflict: What to do if the destination exists: ``rename`` (``name (1).ext``),
            ``skip`` or ``overwrite``.
        recursive: Descend into subdirectories of the source.
        include_hidden: Include dotfiles and files with the hidden attribute.
        ignore: Glob patterns (matched case-insensitively against names) to leave alone.
        follow_symlinks: Follow symlinked directories and files.
        date_source: Date used for ``{year}``/``{month}``: ``modified``, ``created`` or
            ``exif`` (photo capture date, falling back to modified; needs Pillow).
        dedupe: Detect byte-identical files (size -> partial hash -> SHA-256).
        duplicates: ``skip`` leaves duplicates where they are; ``move`` puts them in
            ``duplicates_folder`` under the target.
        fix_extensions: Rename files whose extension lies about their content.
        cleanup_empty_dirs: After executing, remove directories left empty in the source.
        journal: Write an undo journal (to ``<target>/.mediatrace/``) or to a given path.
        max_workers: Threads used for detection and hashing.
        on_progress: ``callback(stage, done, total)`` with stage ``"scan"`` or ``"execute"``.
    """

    def __init__(
        self,
        target: str | os.PathLike[str] | None = None,
        *,
        rules: Iterable[Rule] | None = None,
        detector: FileTypeDetector | None = None,
        conflict: ConflictStrategy = "rename",
        recursive: bool = False,
        include_hidden: bool = False,
        ignore: Iterable[str] = DEFAULT_IGNORE,
        follow_symlinks: bool = False,
        date_source: DateSource = "modified",
        dedupe: bool = False,
        duplicates: Literal["skip", "move"] = "skip",
        duplicates_folder: str = "_Duplicates",
        fix_extensions: bool = False,
        cleanup_empty_dirs: bool = False,
        journal: bool | str | os.PathLike[str] = True,
        max_workers: int | None = None,
        on_progress: ProgressCallback | None = None,
    ):
        if conflict not in ("rename", "skip", "overwrite"):
            raise OrganizerError(f"invalid conflict strategy {conflict!r}")
        if date_source not in ("modified", "created", "exif"):
            raise OrganizerError(f"invalid date_source {date_source!r}")
        if duplicates not in ("skip", "move"):
            raise OrganizerError(f"invalid duplicates mode {duplicates!r}")
        self.target = Path(target).expanduser() if target is not None else None
        self.rules = sort_rules(rules if rules is not None else default_rules())
        self.detector = detector or FileTypeDetector()
        self.conflict = conflict
        self.recursive = recursive
        self.include_hidden = include_hidden
        self.ignore = tuple(p.lower() for p in ignore)
        self.follow_symlinks = follow_symlinks
        self.date_source = date_source
        self.dedupe = dedupe
        self.duplicates = duplicates
        self.duplicates_folder = duplicates_folder
        self.fix_extensions = fix_extensions
        self.cleanup_empty_dirs = cleanup_empty_dirs
        self.journal = journal
        self.max_workers = max_workers
        self.on_progress = on_progress

    # ------------------------------------------------------------------ scan

    def _ignored(self, name: str) -> bool:
        low = name.lower()
        return any(fnmatch.fnmatch(low, pattern) for pattern in self.ignore)

    def _hidden(self, entry: os.DirEntry[str]) -> bool:
        if entry.name.startswith("."):
            return True
        try:
            attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
        except OSError:
            return False
        return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0))

    def scan(self, source: str | os.PathLike[str], *, exclude: Iterable[Path] = ()) -> Iterator[Path]:
        """Yield files under ``source`` that are eligible for organizing."""
        root = Path(source).expanduser().resolve()
        excluded = {path_key(p) for p in exclude}
        stack = [root]
        while stack:
            directory = stack.pop()
            try:
                entries = sorted(os.scandir(directory), key=lambda e: e.name.lower())
            except OSError as exc:
                log.warning("cannot list %s: %s", directory, exc)
                continue
            for entry in entries:
                if self._ignored(entry.name) or (not self.include_hidden and self._hidden(entry)):
                    continue
                try:
                    if entry.is_symlink() and not self.follow_symlinks:
                        continue
                    if entry.is_dir(follow_symlinks=self.follow_symlinks):
                        if self.recursive and path_key(Path(entry.path)) not in excluded:
                            stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=self.follow_symlinks):
                        yield Path(entry.path)
                except OSError as exc:
                    log.warning("cannot stat %s: %s", entry.path, exc)

    def inspect(self, path: str | os.PathLike[str]) -> FileInfo:
        """Detect a file's type and gather the facts rules match on."""
        path = Path(path)
        try:
            st = path.stat()
        except OSError as exc:
            raise DetectionError(f"cannot stat {path}: {exc}") from exc
        detection = self.detector.detect(path)
        modified = datetime.fromtimestamp(st.st_mtime)
        created = datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_ctime))
        date = modified
        if self.date_source == "created":
            date = min(created, modified)
        elif self.date_source == "exif":
            date = (exif_datetime(path) if detection.category == "image" else None) or modified
        return FileInfo(path=path, size=st.st_size, modified=modified, created=created, detection=detection, date=date)

    # ------------------------------------------------------------------ plan

    def _roots(self, source: str | os.PathLike[str]) -> tuple[Path, Path]:
        root = Path(source).expanduser().resolve()
        if not root.is_dir():
            raise OrganizerError(f"source is not a directory: {root}")
        target = self.target.resolve() if self.target is not None else root
        return root, target

    def _progress(self, stage: str, done: int, total: int) -> None:
        if self.on_progress is not None:
            self.on_progress(stage, done, total)

    def plan(
        self, source: str | os.PathLike[str], *, files: Iterable[str | os.PathLike[str]] | None = None
    ) -> OrganizePlan:
        """Work out what would happen, without touching anything.

        ``files`` restricts the plan to specific paths (used by the watcher).
        """
        root, target = self._roots(source)
        exclude = [target] if target != root else []
        paths = [Path(p) for p in files] if files is not None else list(self.scan(root, exclude=exclude))
        plan = OrganizePlan(source_root=root, target_root=target)

        infos: list[FileInfo] = []
        total = len(paths)

        def _inspect(p: Path) -> FileInfo | Operation:
            try:
                return self.inspect(p)
            except DetectionError as exc:
                return Operation(p, None, Action.SKIP, reason=f"unreadable: {exc}", status=OpStatus.SKIPPED)

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            for i, item in enumerate(pool.map(_inspect, paths), 1):
                if isinstance(item, Operation):
                    plan.operations.append(item)
                else:
                    infos.append(item)
                self._progress("scan", i, total)

        duplicate_of = self._find_duplicates(infos) if self.dedupe else {}
        reserved: set[str] = set()
        now = datetime.now()

        for info in sorted(infos, key=lambda i: str(i.path).lower()):
            op = self._plan_one(info, target, duplicate_of, reserved, now)
            plan.operations.append(op)
        return plan

    def _plan_one(
        self,
        info: FileInfo,
        target: Path,
        duplicate_of: dict[str, Path],
        reserved: set[str],
        now: datetime,
    ) -> Operation:
        def op(action: Action, dest: Path | None, rule: str | None, reason: str = "") -> Operation:
            return Operation(info.path, dest, action, rule, reason, info.detection, info.size)

        original = duplicate_of.get(path_key(info.path))
        if original is not None:
            if self.duplicates == "skip":
                return op(Action.SKIP, None, "duplicates", f"duplicate of {original}")
            dest = target / self.duplicates_folder / info.path.name
            rule_name, action = "duplicates", Action.MOVE
            reason = f"duplicate of {original}"
        else:
            rule = next((r for r in self.rules if r.matches(info, now)), None)
            if rule is None:
                return op(Action.SKIP, None, None, "no matching rule")
            if rule.action is Action.SKIP:
                return op(Action.SKIP, None, rule.name, "excluded by rule")
            dest = target / rule.render_destination(info) / rule.render_name(info, fix_extension=self.fix_extensions)
            rule_name, action, reason = rule.name, rule.action, ""

        if path_key(dest) == path_key(info.path):
            return op(Action.SKIP, None, rule_name, "already in place")

        key = path_key(dest)
        if dest.exists() or key in reserved:
            if self.dedupe and key not in reserved and self._same_content(info.path, dest):
                return op(Action.SKIP, None, "duplicates", f"identical file already at {dest}")
            if self.conflict == "skip":
                return op(Action.SKIP, None, rule_name, f"destination exists: {dest}")
            if self.conflict == "rename" or key in reserved:
                dest = unique_path(dest, reserved)
                reason = (reason + "; " if reason else "") + "renamed to avoid a conflict"
            else:
                reason = (reason + "; " if reason else "") + "overwrites existing file"
        reserved.add(path_key(dest))
        return op(action, dest, rule_name, reason)

    @staticmethod
    def _same_content(a: Path, b: Path) -> bool:
        try:
            return a.stat().st_size == b.stat().st_size and file_digest(a) == file_digest(b)
        except OSError:
            return False

    def _find_duplicates(self, infos: list[FileInfo]) -> dict[str, Path]:
        """Map path_key(duplicate) -> the path of the copy that is kept."""
        by_size: dict[int, list[FileInfo]] = defaultdict(list)
        for info in infos:
            if info.size > 0:
                by_size[info.size].append(info)
        candidates = [group for group in by_size.values() if len(group) > 1]
        if not candidates:
            return {}

        def _hash(fn: Callable[[Path], str], group: list[FileInfo]) -> list[list[FileInfo]]:
            buckets: dict[str, list[FileInfo]] = defaultdict(list)
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                for info, digest in zip(group, pool.map(lambda i: _safe(fn, i.path), group)):
                    if digest:
                        buckets[digest].append(info)
            return [b for b in buckets.values() if len(b) > 1]

        result: dict[str, Path] = {}
        for group in candidates:
            for partial in _hash(partial_digest, group):
                for full in _hash(file_digest, partial):
                    # Keep the oldest file; path order breaks ties deterministically.
                    full.sort(key=lambda i: (i.modified, str(i.path).lower()))
                    keeper = full[0].path
                    for dup in full[1:]:
                        result[path_key(dup.path)] = keeper
        return result

    # --------------------------------------------------------------- execute

    def execute(self, plan: OrganizePlan) -> OrganizeReport:
        """Carry out a plan. Failures are recorded per operation; nothing is raised."""
        started = time.perf_counter()
        entries: list[dict[str, Any]] = []
        todo = [op for op in plan.operations if op.status is OpStatus.PLANNED]
        for i, op in enumerate(todo, 1):
            if op.action is Action.SKIP or op.destination is None:
                op.status = OpStatus.SKIPPED
                continue
            try:
                self._apply(op)
            except OSError as exc:
                op.status = OpStatus.FAILED
                op.error = str(exc)
                log.warning("failed to %s %s: %s", op.action, op.source, exc)
            else:
                if op.status is OpStatus.DONE:
                    entries.append(
                        {"action": str(op.action), "source": str(op.source), "destination": str(op.destination)}
                    )
            self._progress("execute", i, len(todo))

        removed = self._remove_empty_dirs(plan) if self.cleanup_empty_dirs else []
        journal_path = self._write_journal(plan, entries) if entries and self.journal else None
        return OrganizeReport(
            plan=plan, journal_path=journal_path, elapsed=time.perf_counter() - started, removed_dirs=removed
        )

    def _apply(self, op: Operation) -> None:
        assert op.destination is not None
        dest = op.destination
        if not op.source.exists():
            op.status, op.reason = OpStatus.SKIPPED, "source disappeared"
            return
        if dest.exists() and self.conflict != "overwrite":
            # Something appeared since planning.
            if self.conflict == "skip":
                op.status, op.reason = OpStatus.SKIPPED, f"destination exists: {dest}"
                return
            dest = op.destination = unique_path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if op.action is Action.COPY:
            shutil.copy2(op.source, dest)
        elif self.conflict == "overwrite":
            try:
                os.replace(op.source, dest)
            except OSError:
                shutil.move(str(op.source), str(dest))  # cross-device
        else:
            shutil.move(str(op.source), str(dest))
        op.status = OpStatus.DONE

    def organize(self, source: str | os.PathLike[str], *, dry_run: bool = False) -> OrganizeReport:
        """Plan and (unless ``dry_run``) execute in one call."""
        plan = self.plan(source)
        if dry_run:
            return OrganizeReport(plan=plan, dry_run=True)
        return self.execute(plan)

    def _remove_empty_dirs(self, plan: OrganizePlan) -> list[Path]:
        removed: list[Path] = []
        root, target = plan.source_root, plan.target_root
        protected = {path_key(root), path_key(target)}
        candidates = {op.source.parent for op in plan.operations if op.status is OpStatus.DONE}
        for directory in sorted(candidates, key=lambda p: len(p.parts), reverse=True):
            current = directory
            while path_key(current) not in protected and root in current.parents:
                try:
                    current.rmdir()  # only succeeds when empty
                except OSError:
                    break
                removed.append(current)
                current = current.parent
        return removed

    # --------------------------------------------------------------- journal

    def _write_journal(self, plan: OrganizePlan, entries: list[dict[str, Any]]) -> Path | None:
        if isinstance(self.journal, (str, os.PathLike)):
            path = Path(self.journal)
        else:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            path = plan.target_root / JOURNAL_DIR / f"journal-{stamp}.json"
        data = {
            "version": JOURNAL_VERSION,
            "created": datetime.now().isoformat(timespec="seconds"),
            "source_root": str(plan.source_root),
            "target_root": str(plan.target_root),
            "operations": entries,
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning("could not write undo journal %s: %s", path, exc)
            return None
        return path

    @staticmethod
    def undo(journal_path: str | os.PathLike[str]) -> UndoReport:
        """Reverse the operations recorded in a journal (moves go back, copies are removed)."""
        path = Path(journal_path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entries = data["operations"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise JournalError(f"cannot read journal {path}: {exc}") from exc
        if data.get("undone"):
            raise JournalError(f"journal {path} was already undone")

        report = UndoReport(journal_path=path)
        for entry in reversed(entries):
            src, dest = Path(entry["source"]), Path(entry["destination"])
            try:
                if entry["action"] == Action.MOVE:
                    if not dest.exists():
                        report.skipped.append(f"{dest} no longer exists")
                        continue
                    if src.exists():
                        report.skipped.append(f"{src} already exists; left {dest} in place")
                        continue
                    src.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dest), str(src))
                    report.restored.append(src)
                elif entry["action"] == Action.COPY:
                    if dest.exists():
                        dest.unlink()
                        report.removed_copies.append(dest)
            except OSError as exc:
                report.failed.append(f"{dest}: {exc}")
            else:
                _prune_empty_parents(dest.parent, Path(data.get("target_root", dest.parent)))

        data["undone"] = datetime.now().isoformat(timespec="seconds")
        try:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass
        return report


def _safe(fn: Callable[[Path], str], path: Path) -> str | None:
    try:
        return fn(path)
    except OSError as exc:
        log.warning("cannot hash %s: %s", path, exc)
        return None


def _prune_empty_parents(directory: Path, stop: Path) -> None:
    stop_key = path_key(stop)
    while path_key(directory) != stop_key and stop in directory.parents:
        try:
            directory.rmdir()
        except OSError:
            return
        directory = directory.parent
