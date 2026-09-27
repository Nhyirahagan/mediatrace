"""Command-line interface: ``mediatrace detect|organize|undo|watch|info|fetch|clean-url``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .detection import FileTypeDetector
from .exceptions import MediaTraceError
from .media import DownloadOptions, MediaClient, ProgressEvent, detect_platform, extract_urls, normalize_url
from .media.cleaning import CleanOptions
from .media.client import DEFAULT_TEMPLATE
from .naming import human_size
from .organizer import PRESETS, Action, FileOrganizer, FolderWatcher, OpStatus, load_rules, preset


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _configure_stdout() -> None:
    # Windows consoles default to a legacy code page; don't crash on emoji file names.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


# ----------------------------------------------------------------------- detect


def cmd_detect(args: argparse.Namespace) -> int:
    detector = FileTypeDetector(use_libmagic=args.libmagic)
    paths: list[Path] = []
    for raw in args.paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            pattern = "**/*" if args.recursive else "*"
            paths.extend(sorted(x for x in p.glob(pattern) if x.is_file()))
        else:
            paths.append(p)
    results = detector.detect_many(paths)
    if args.json:
        _print_json([results[p].to_dict() for p in paths if p in results])
        return 0 if len(results) == len(paths) else 1
    for p in paths:
        r = results.get(p)
        if r is None:
            print(f"{p}: unreadable")
            continue
        flag = f"  !! extension says .{r.declared_extension}, content is .{r.extension}" if r.extension_mismatch else ""
        print(f"{p}: {r.mime_type} [{r.category}] {r.description} ({r.confidence:.0%}, {r.method}){flag}")
    return 0 if len(results) == len(paths) else 1


# --------------------------------------------------------------------- organize


def _build_organizer(args: argparse.Namespace) -> FileOrganizer:
    if args.rules:
        rules = load_rules(args.rules)
    else:
        rules = preset(args.preset)
    if args.copy:
        for rule in rules:
            if rule.action is Action.MOVE:
                rule.action = Action.COPY

    def progress(stage: str, done: int, total: int) -> None:
        if sys.stderr.isatty() and total:
            print(f"\r{stage}: {done}/{total}", end="" if done < total else "\n", file=sys.stderr, flush=True)

    return FileOrganizer(
        args.target,
        rules=rules,
        conflict=args.conflict,
        recursive=args.recursive,
        include_hidden=args.include_hidden,
        date_source=args.date_source,
        dedupe=args.dedupe is not None,
        duplicates=args.dedupe or "skip",
        fix_extensions=args.fix_extensions,
        cleanup_empty_dirs=args.cleanup,
        journal=not args.no_journal,
        on_progress=None if getattr(args, "json", False) else progress,
    )


def _print_report(report, verbose: bool) -> None:
    root = report.plan.target_root
    for op in report.operations:
        if op.status is OpStatus.SKIPPED and not verbose and op.action == "skip":
            continue
        if op.destination is not None:
            try:
                dest = op.destination.relative_to(root)
            except ValueError:
                dest = op.destination
            line = f"{op.action:<5} {op.source.name} -> {dest}"
        else:
            line = f"skip  {op.source.name}"
        extra = op.error or op.reason
        status = "" if op.status in (OpStatus.DONE, OpStatus.PLANNED) else f" [{op.status}]"
        print(line + status + (f"  ({extra})" if extra else ""))
    s = report.summary()
    print(
        f"\n{'DRY RUN - ' if report.dry_run else ''}{s['files']} files, "
        f"{human_size(s['bytes'])} to process; "
        + ", ".join(f"{k}: {v}" for k, v in sorted(s["statuses"].items()))
    )
    if s["categories"]:
        print("by category: " + ", ".join(f"{k} {v}" for k, v in s["categories"].items()))
    if report.journal_path:
        print(f"undo with: mediatrace undo \"{report.journal_path}\"")


def cmd_organize(args: argparse.Namespace) -> int:
    organizer = _build_organizer(args)
    report = organizer.organize(args.source, dry_run=args.dry_run)
    if args.json:
        _print_json(report.to_dict())
    else:
        _print_report(report, args.verbose > 0)
    return 0 if report.ok else 1


def cmd_undo(args: argparse.Namespace) -> int:
    report = FileOrganizer.undo(args.journal)
    print(f"restored {len(report.restored)} files, removed {len(report.removed_copies)} copies")
    for msg in report.skipped:
        print(f"skipped: {msg}")
    for msg in report.failed:
        print(f"failed: {msg}", file=sys.stderr)
    return 0 if report.ok else 1


def cmd_watch(args: argparse.Namespace) -> int:
    organizer = _build_organizer(args)
    organizer.on_progress = None

    def on_report(report) -> None:
        for op in report.done:
            print(f"{op.action} {op.source.name} -> {op.destination}")
        for op in report.failed:
            print(f"failed {op.source.name}: {op.error}", file=sys.stderr)

    watcher = FolderWatcher(organizer, args.source, interval=args.interval, settle=args.settle, on_report=on_report)
    print(f"watching {watcher.source} (Ctrl+C to stop)")
    try:
        watcher.run()
    except KeyboardInterrupt:
        pass
    return 0


# ------------------------------------------------------------------------ media


def _progress_printer():
    if not sys.stderr.isatty():
        return None

    def show(event: ProgressEvent) -> None:
        if event.status == "downloading":
            frac = event.fraction
            pct = f"{frac:6.1%}" if frac is not None else "   ..."
            done = human_size(event.downloaded_bytes or 0)
            speed = f"{human_size(event.speed)}/s" if event.speed else ""
            print(f"\r  {pct}  {done:>10}  {speed:>12}  {event.filename or ''}"[:120], end="", file=sys.stderr)
        elif event.status == "finished":
            print(file=sys.stderr)

    return show


def cmd_info(args: argparse.Namespace) -> int:
    client = MediaClient(options=DownloadOptions(cookies_file=args.cookies, playlist=args.playlist))
    info = client.info(args.url)
    if args.json:
        _print_json(info.to_dict())
        return 0
    print(f"{info.title or '(untitled)'}")
    print(f"  platform : {info.platform}")
    print(f"  uploader : {info.uploader}")
    print(f"  id       : {info.id}")
    print(f"  type     : {info.media_type}")
    if info.upload_date:
        print(f"  date     : {info.upload_date}")
    if info.duration:
        print(f"  duration : {int(info.duration) // 60}:{int(info.duration) % 60:02d}")
    if info.entries:
        print(f"  entries  : {len(info.entries)}")
        for i, e in enumerate(info.entries[:20], 1):
            print(f"    {i:>3}. {e.title or e.id}")
    if args.formats and info.formats:
        print("  formats:")
        for f in info.formats:
            size = human_size(f.filesize) if f.filesize else ""
            codecs = "+".join(c for c in (f.vcodec, f.acodec) if c)
            print(f"    {f.format_id:>10}  {f.ext:<5} {f.resolution:<12} {codecs:<24} {size}")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    urls = list(args.urls)
    if args.input:
        text = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
        urls.extend(extract_urls(text))
    if not urls:
        print("no URLs given", file=sys.stderr)
        return 2

    clean: CleanOptions | bool = False
    if not args.no_clean:
        clean = CleanOptions(
            strip_metadata=not args.keep_metadata,
            strip_av_metadata=not args.keep_metadata,
            image_format=args.image_format,
            ascii_names=args.ascii,
        )
    options = DownloadOptions(
        quality=args.quality,
        audio_only=args.audio_only,
        audio_format=args.audio_format,
        playlist=args.playlist,
        subtitles=args.subs,
        thumbnail=args.thumbnail,
        write_metadata=args.metadata,
        cookies_file=args.cookies,
        proxy=args.proxy,
        rate_limit=args.rate_limit,
        max_filesize=args.max_filesize,
        progress=None if args.json or len(urls) > 1 else _progress_printer(),
    )
    client = MediaClient(
        args.output, options=options, clean=clean, filename_template=args.template, conflict=args.conflict
    )
    batch = client.download_many(urls, max_workers=args.workers)
    if args.json:
        _print_json(
            {
                "results": [
                    {
                        "url": r.url,
                        "provider": r.provider,
                        "info": r.info.to_dict(),
                        "files": [
                            {"path": str(f.path), "kind": f.kind, "steps": f.steps, "skipped": f.skipped}
                            for f in r.files
                        ],
                    }
                    for r in batch.results
                ],
                "errors": {u: str(e) for u, e in batch.errors.items()},
            }
        )
    else:
        for r in batch.results:
            print(f"[{r.provider}] {r.info.title or r.url}")
            for f in r.files:
                note = " (kept existing)" if f.skipped else (f"  [{', '.join(f.steps)}]" if f.steps else "")
                print(f"  {f.kind:<9} {f.path}{note}")
        for u, e in batch.errors.items():
            print(f"error: {u}: {e}", file=sys.stderr)
    return 0 if batch.ok else 1


def cmd_clean_url(args: argparse.Namespace) -> int:
    code = 0
    for url in args.urls:
        try:
            print(f"{normalize_url(url)}\t{detect_platform(url)}")
        except MediaTraceError as exc:
            print(f"error: {url}: {exc}", file=sys.stderr)
            code = 1
    return code


# ----------------------------------------------------------------------- parser


def _add_organize_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("source", help="directory to organize")
    p.add_argument("-t", "--target", help="destination root (default: organize in place)")
    rules = p.add_mutually_exclusive_group()
    rules.add_argument("--preset", default="category", choices=sorted(PRESETS), help="built-in rule set")
    rules.add_argument("--rules", help="rules file (.toml or .json)")
    p.add_argument("--copy", action="store_true", help="copy instead of move")
    p.add_argument("-r", "--recursive", action="store_true", help="include subdirectories")
    p.add_argument("--include-hidden", action="store_true")
    p.add_argument("--conflict", choices=["rename", "skip", "overwrite"], default="rename")
    p.add_argument("--date-source", choices=["modified", "created", "exif"], default="modified")
    p.add_argument(
        "--dedupe", nargs="?", const="skip", choices=["skip", "move"],
        help="detect identical files; 'skip' leaves them, 'move' puts them in _Duplicates",
    )
    p.add_argument("--fix-extensions", action="store_true", help="correct extensions that lie about content")
    p.add_argument("--cleanup", action="store_true", help="remove directories left empty")
    p.add_argument("--no-journal", action="store_true", help="do not write an undo journal")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mediatrace", description=__doc__)
    parser.add_argument("--version", action="version", version=f"mediatrace {__version__}")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("detect", help="identify file types by content")
    p.add_argument("paths", nargs="+")
    p.add_argument("-r", "--recursive", action="store_true")
    p.add_argument("--libmagic", action="store_true", help="fall back to libmagic when installed")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("organize", help="sort a directory")
    _add_organize_options(p)
    p.add_argument("-n", "--dry-run", action="store_true", help="show the plan without changing anything")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_organize)

    p = sub.add_parser("undo", help="reverse an organize run from its journal")
    p.add_argument("journal")
    p.set_defaults(func=cmd_undo)

    p = sub.add_parser("watch", help="keep a directory organized")
    _add_organize_options(p)
    p.add_argument("--interval", type=float, default=5.0, help="seconds between scans")
    p.add_argument("--settle", type=float, default=5.0, help="seconds a file must be unchanged")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("info", help="show media metadata for a URL")
    p.add_argument("url")
    p.add_argument("--formats", action="store_true", help="list available formats")
    p.add_argument("--playlist", action="store_true")
    p.add_argument("--cookies", help="Netscape cookies.txt file")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("fetch", help="download and clean media")
    p.add_argument("urls", nargs="*")
    p.add_argument("-i", "--input", help="read URLs from a text file ('-' for stdin)")
    p.add_argument("-o", "--output", default="downloads")
    p.add_argument("-q", "--quality", default="best", help="best, worst, audio, 1080p, 720p, 4k...")
    p.add_argument("-x", "--audio-only", action="store_true")
    p.add_argument("--audio-format", default="mp3")
    p.add_argument("--playlist", action="store_true", help="download whole playlists")
    p.add_argument("--subs", action="store_true", help="download subtitles")
    p.add_argument("--thumbnail", action="store_true")
    p.add_argument("--metadata", action="store_true", help="write <name>.info.json")
    p.add_argument("--template", default=DEFAULT_TEMPLATE, help="output path template (no extension)")
    p.add_argument("--conflict", choices=["rename", "skip", "overwrite"], default="rename")
    p.add_argument("--no-clean", action="store_true", help="skip detection/cleaning")
    p.add_argument("--keep-metadata", action="store_true", help="do not strip EXIF/container tags")
    p.add_argument("--image-format", choices=["jpg", "png", "webp"])
    p.add_argument("--ascii", action="store_true", help="ASCII-only file names")
    p.add_argument("--cookies", help="Netscape cookies.txt for content you can access")
    p.add_argument("--proxy")
    p.add_argument("--rate-limit", help="e.g. 2M")
    p.add_argument("--max-filesize", help="e.g. 500MB")
    p.add_argument("-w", "--workers", type=int, default=3)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("clean-url", help="strip tracking parameters and identify the platform")
    p.add_argument("urls", nargs="+")
    p.set_defaults(func=cmd_clean_url)
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    level = logging.WARNING - 10 * min(args.verbose, 2)
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    try:
        return args.func(args)
    except MediaTraceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
