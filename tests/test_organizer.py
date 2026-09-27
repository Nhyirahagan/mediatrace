from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pytest

from mediatrace import FileOrganizer, FolderWatcher, Rule, load_rules, preset
from mediatrace.exceptions import JournalError, OrganizerError, RuleError
from mediatrace.organizer import Action, OpStatus

from .conftest import JPEG, MP4, PDF, PNG, SAMPLES, WEBP


@pytest.fixture
def messy(tmp_path: Path, make_file) -> Path:
    src = tmp_path / "messy"
    make_file("holiday.jpg", JPEG, directory=src)
    make_file("scan.pdf", PDF, directory=src)
    make_file("clip.mp4", MP4, directory=src)
    make_file("report.docx", SAMPLES["docx"], directory=src)
    make_file("notes.txt", "remember the milk\n", directory=src)
    make_file("disguised.jpg", PNG, directory=src)  # really a PNG
    make_file("download.crdownload", b"partial", directory=src)  # ignored by default
    make_file(".hidden", "secret", directory=src)
    make_file("sub/deep.png", PNG, directory=src)
    return src


def rel(paths, root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in paths}


def test_plan_does_not_touch_files(messy, tmp_path):
    before = sorted(p.name for p in messy.rglob("*"))
    plan = FileOrganizer(tmp_path / "out").plan(messy)
    assert sorted(p.name for p in messy.rglob("*")) == before
    assert not (tmp_path / "out").exists()
    assert {op.source.name for op in plan} == {
        "holiday.jpg", "scan.pdf", "clip.mp4", "report.docx", "notes.txt", "disguised.jpg"
    }


def test_organize_by_category(messy, tmp_path):
    out = tmp_path / "out"
    report = FileOrganizer(out).organize(messy)
    assert report.ok
    files = rel((p for p in out.rglob("*") if p.is_file() and ".mediatrace" not in p.parts), out)
    assert files == {
        "Images/holiday.jpg",
        "Images/disguised.jpg",
        "Documents/scan.pdf",
        "Documents/report.docx",
        "Videos/clip.mp4",
        "Text/notes.txt",
    }
    assert (messy / "sub" / "deep.png").exists()  # not recursive by default
    assert (messy / "download.crdownload").exists()
    assert (messy / ".hidden").exists()


def test_recursive_and_cleanup(messy, tmp_path):
    out = tmp_path / "out"
    FileOrganizer(out, recursive=True, cleanup_empty_dirs=True).organize(messy)
    assert (out / "Images" / "deep.png").exists()
    assert not (messy / "sub").exists()


def test_fix_extensions(messy, tmp_path):
    out = tmp_path / "out"
    FileOrganizer(out, fix_extensions=True).organize(messy)
    assert (out / "Images" / "disguised.png").exists()


def test_in_place_is_idempotent(messy):
    org = FileOrganizer(recursive=True)
    first = org.organize(messy)
    assert first.done
    second = org.organize(messy)
    assert not second.done
    assert all(op.reason == "already in place" for op in second.operations if op.action is Action.SKIP)


def test_conflict_rename(tmp_path, make_file):
    src, out = tmp_path / "src", tmp_path / "out"
    make_file("a.png", PNG, directory=src)
    make_file("Images/a.png", b"\x89PNG\r\n\x1a\nother", directory=out)
    FileOrganizer(out).organize(src)
    assert (out / "Images" / "a (1).png").exists()


def test_conflict_skip(tmp_path, make_file):
    src, out = tmp_path / "src", tmp_path / "out"
    make_file("a.png", PNG, directory=src)
    make_file("Images/a.png", b"\x89PNG\r\n\x1a\nother", directory=out)
    report = FileOrganizer(out, conflict="skip").organize(src)
    assert (src / "a.png").exists()
    assert report.operations[0].status is OpStatus.SKIPPED


def test_conflict_overwrite(tmp_path, make_file):
    src, out = tmp_path / "src", tmp_path / "out"
    make_file("a.png", PNG, directory=src)
    make_file("Images/a.png", b"\x89PNG\r\n\x1a\nother", directory=out)
    FileOrganizer(out, conflict="overwrite").organize(src)
    assert (out / "Images" / "a.png").read_bytes() == PNG
    assert not (src / "a.png").exists()


def test_same_name_in_one_plan_gets_renamed(tmp_path, make_file):
    src, out = tmp_path / "src", tmp_path / "out"
    make_file("x/a.png", PNG, directory=src)
    make_file("y/a.png", PNG + b"different", directory=src)
    FileOrganizer(out, recursive=True).organize(src)
    assert {p.name for p in (out / "Images").iterdir()} == {"a.png", "a (1).png"}


def test_dedupe_skip_and_move(tmp_path, make_file):
    src = tmp_path / "src"
    make_file("one.png", PNG, directory=src, mtime=1_000_000)
    make_file("two.png", PNG, directory=src, mtime=2_000_000)
    make_file("other.png", PNG + b"x", directory=src)
    plan = FileOrganizer(tmp_path / "out", dedupe=True).plan(src)
    dup = next(op for op in plan if op.source.name == "two.png")
    assert dup.action is Action.SKIP and "duplicate of" in dup.reason and "one.png" in dup.reason

    report = FileOrganizer(tmp_path / "out", dedupe=True, duplicates="move").organize(src)
    assert report.ok
    assert (tmp_path / "out" / "_Duplicates" / "two.png").exists()
    assert (tmp_path / "out" / "Images" / "one.png").exists()


def test_dedupe_against_existing_destination(tmp_path, make_file):
    src, out = tmp_path / "src", tmp_path / "out"
    make_file("a.png", PNG, directory=src)
    make_file("Images/a.png", PNG, directory=out)
    report = FileOrganizer(out, dedupe=True).organize(src)
    assert report.operations[0].action is Action.SKIP
    assert "identical" in report.operations[0].reason


def test_copy_rule_and_undo_removes_copies(tmp_path, make_file):
    src, out = tmp_path / "src", tmp_path / "out"
    make_file("a.png", PNG, directory=src)
    report = FileOrganizer(out, rules=[Rule("{category}", action="copy")]).organize(src)
    assert (src / "a.png").exists() and (out / "Images" / "a.png").exists()
    undo = FileOrganizer.undo(report.journal_path)
    assert undo.removed_copies == [out / "Images" / "a.png"]
    assert (src / "a.png").exists()


def test_undo_restores_moves(messy, tmp_path):
    out = tmp_path / "out"
    report = FileOrganizer(out).organize(messy)
    assert report.journal_path and report.journal_path.exists()
    undo = FileOrganizer.undo(report.journal_path)
    assert undo.ok
    assert (messy / "holiday.jpg").exists()
    assert not (out / "Images").exists()  # emptied dirs pruned
    with pytest.raises(JournalError):
        FileOrganizer.undo(report.journal_path)


def test_rules_priority_and_conditions(tmp_path, make_file):
    src, out = tmp_path / "src", tmp_path / "out"
    make_file("Screenshot 2024.png", PNG, directory=src)
    make_file("cat.png", PNG + b"1", directory=src)
    make_file("big.mp4", MP4 + b"\x00" * 5000, directory=src)
    make_file("tiny.mp4", MP4, directory=src)
    rules = [
        Rule("Other/{ext}"),
        Rule("Screens", name_pattern=r"^screenshot", priority=10),
        Rule("BigVideos", categories=["video"], min_size="4KB", priority=5),
        Rule("", action="skip", extensions=["mp4"], max_size=1000, priority=6),
    ]
    FileOrganizer(out, rules=rules).organize(src)
    assert (out / "Screens" / "Screenshot 2024.png").exists()
    assert (out / "Other" / "png" / "cat.png").exists()
    assert (out / "BigVideos" / "big.mp4").exists()
    assert (src / "tiny.mp4").exists()


def test_date_template_and_rename(tmp_path, make_file):
    src, out = tmp_path / "src", tmp_path / "out"
    ts = datetime(2023, 7, 14, 12, 0).timestamp()
    make_file("IMG_001.jpg", JPEG, directory=src, mtime=ts)
    rules = [Rule("{category}/{year}/{month_name}", rename="{date}_{stem}")]
    FileOrganizer(out, rules=rules).organize(src)
    assert (out / "Images" / "2023" / "July" / "2023-07-14_IMG_001.jpg").exists()


def test_age_conditions(tmp_path, make_file):
    src, out = tmp_path / "src", tmp_path / "out"
    make_file("old.pdf", PDF, directory=src, mtime=time.time() - 90 * 86400)
    make_file("new.pdf", PDF + b" ", directory=src)
    rules = [Rule("Archive", older_than="30d"), Rule("Recent", newer_than="1w")]
    FileOrganizer(out, rules=rules).organize(src)
    assert (out / "Archive" / "old.pdf").exists()
    assert (out / "Recent" / "new.pdf").exists()


def test_no_matching_rule_leaves_file(tmp_path, make_file):
    src = tmp_path / "src"
    make_file("a.pdf", PDF, directory=src)
    report = FileOrganizer(tmp_path / "out", rules=[Rule("Pics", categories=["image"])]).organize(src)
    assert report.operations[0].reason == "no matching rule"
    assert (src / "a.pdf").exists()


def test_target_inside_source_is_not_rescanned(tmp_path, make_file):
    src = tmp_path / "src"
    make_file("a.png", PNG, directory=src)
    org = FileOrganizer(src / "Sorted", recursive=True)
    org.organize(src)
    assert not org.plan(src).operations


def test_presets():
    for name in ("category", "category-date", "date", "extension", "media-timeline", "downloads"):
        assert preset(name)
    with pytest.raises(RuleError):
        preset("nope")


def test_invalid_rules():
    with pytest.raises(RuleError):
        Rule("{bogus}")
    with pytest.raises(RuleError):
        Rule("../escape")
    with pytest.raises(RuleError):
        Rule("x", categories=["not-a-category"])
    with pytest.raises(RuleError):
        Rule("x", min_size="lots")
    with pytest.raises(RuleError):
        Rule("x", rename="a/b")
    with pytest.raises(OrganizerError):
        FileOrganizer(conflict="explode")  # type: ignore[arg-type]


def test_template_values_cannot_escape(tmp_path, make_file):
    src, out = tmp_path / "src", tmp_path / "out"
    make_file("..evil.png", PNG, directory=src)
    FileOrganizer(out, rules=[Rule("{stem}")], include_hidden=True).organize(src)
    assert all(out in p.parents for p in out.rglob("*"))


def test_load_rules_toml_and_json(tmp_path):
    toml = tmp_path / "rules.toml"
    toml.write_text(
        """
[[rules]]
name = "photos"
categories = ["image"]
destination = "Photos/{year}"
priority = 10

[[rules]]
ext = "pdf, epub"
to = "Reading"

[[rules]]
action = "skip"
glob = "*.iso"
""",
        encoding="utf-8",
    )
    rules = load_rules(toml)
    assert [r.name for r in rules] == ["photos", "Reading", "skip"]
    assert rules[1].extensions == frozenset({"pdf", "epub"})

    js = tmp_path / "rules.json"
    js.write_text(json.dumps({"rules": [r.to_dict() for r in rules]}), encoding="utf-8")
    again = load_rules(js)
    assert [r.destination for r in again] == [r.destination for r in rules]


def test_progress_callback(messy, tmp_path):
    events = []
    FileOrganizer(tmp_path / "out", on_progress=lambda s, d, t: events.append((s, d, t))).organize(messy)
    assert events[-1][0] == "execute"
    assert any(stage == "scan" for stage, _, _ in events)


def test_watcher_organizes_settled_files(tmp_path, make_file):
    src, out = tmp_path / "inbox", tmp_path / "out"
    src.mkdir()
    watcher = FolderWatcher(FileOrganizer(out), src, interval=0, settle=0)
    make_file("a.webp", WEBP, directory=src)
    assert watcher.poll() is None  # first sighting
    report = watcher.poll()
    assert report is not None and report.done
    assert (out / "Images" / "a.webp").exists()
    assert watcher.poll() is None


def test_watcher_run_loop(tmp_path, make_file):
    src, out = tmp_path / "inbox", tmp_path / "out"
    make_file("a.pdf", PDF, directory=src)
    watcher = FolderWatcher(FileOrganizer(out), src, interval=0, settle=0)
    watcher.run(max_polls=3)
    assert (out / "Documents" / "a.pdf").exists()


def test_report_serializes(messy, tmp_path):
    report = FileOrganizer(tmp_path / "out").organize(messy, dry_run=True)
    data = json.loads(json.dumps(report.to_dict()))
    assert data["summary"]["dry_run"] is True
    assert data["summary"]["statuses"] == {"planned": 6}
    assert "would process" in str(report)
