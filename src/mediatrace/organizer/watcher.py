"""Polling folder watcher: organizes new files once they stop changing."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ..naming import path_key
from .organizer import FileOrganizer, OpStatus, OrganizeReport

log = logging.getLogger(__name__)


class FolderWatcher:
    """Watch a directory and organize files after they have been stable for ``settle`` seconds.

    Uses polling (no extra dependencies, works on network drives). A file is
    considered ready when its size and mtime are unchanged across polls for at
    least ``settle`` seconds, so half-written downloads are left alone.

    >>> watcher = FolderWatcher(FileOrganizer("~/Sorted"), "~/Downloads", interval=5)
    >>> watcher.start()   # background thread
    >>> watcher.stop()
    """

    def __init__(
        self,
        organizer: FileOrganizer,
        source: str | os.PathLike[str],
        *,
        interval: float = 5.0,
        settle: float = 5.0,
        on_report: Callable[[OrganizeReport], None] | None = None,
    ):
        self.organizer = organizer
        self.source = Path(source).expanduser().resolve()
        self.interval = interval
        self.settle = settle
        self.on_report = on_report
        self._seen: dict[str, tuple[tuple[int, int], float]] = {}
        self._left_alone: dict[str, tuple[int, int]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll(self) -> OrganizeReport | None:
        """Run one polling cycle; returns a report if anything was organized."""
        now = time.monotonic()
        target = self.organizer.target.resolve() if self.organizer.target else self.source
        exclude = [target] if target != self.source else []
        ready: list[Path] = []
        present: set[str] = set()
        for path in self.organizer.scan(self.source, exclude=exclude):
            key = path_key(path)
            try:
                st = path.stat()
            except OSError:
                continue
            sig = (st.st_size, st.st_mtime_ns)
            present.add(key)
            if self._left_alone.get(key) == sig:
                continue
            previous = self._seen.get(key)
            if previous is None or previous[0] != sig:
                self._seen[key] = (sig, now)
            elif now - previous[1] >= self.settle:
                ready.append(path)
        for key in set(self._seen) - present:
            del self._seen[key]
        for key in set(self._left_alone) - present:
            del self._left_alone[key]
        if not ready:
            return None

        report = self.organizer.execute(self.organizer.plan(self.source, files=ready))
        for op in report.operations:
            key = path_key(op.source)
            self._seen.pop(key, None)
            if op.status in (OpStatus.SKIPPED, OpStatus.FAILED) and op.source.exists():
                try:
                    st = op.source.stat()
                    self._left_alone[key] = (st.st_size, st.st_mtime_ns)
                except OSError:
                    pass
        if report.done or report.failed:
            log.info("%s", report)
            if self.on_report is not None:
                self.on_report(report)
            return report
        return None

    def run(self, *, max_polls: int | None = None) -> None:
        """Poll in the current thread until :meth:`stop` is called (or ``max_polls`` is reached)."""
        polls = 0
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception:  # keep watching even if one cycle fails
                log.exception("watch cycle failed")
            polls += 1
            if max_polls is not None and polls >= max_polls:
                break
            self._stop.wait(self.interval)

    def start(self) -> threading.Thread:
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="mediatrace-watcher", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self, timeout: float | None = None) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
