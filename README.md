# mediatrace

A Python library (plus CLI) with two cores:

1. **Smart file organizer**: sorts messy directories using *content-based* file-type detection (magic bytes, container inspection, text heuristics), not just extensions.
2. **Unified media downloader**: one API to fetch media from public platforms (YouTube, TikTok, Instagram, X, Reddit, Vimeo, SoundCloud, direct links, and many more via yt-dlp). It also cleans what it downloads: it fixes wrong extensions, strips EXIF/GPS and container metadata, rejects HTML error pages, and gives files safe, template-based names.

The core has **zero dependencies**. Optional extras:

```bash
pip install mediatrace            # detection + organizer + direct downloads
pip install "mediatrace[media]"   # + yt-dlp for platform URLs
pip install "mediatrace[images]"  # + Pillow for EXIF dates / metadata stripping / conversion
pip install "mediatrace[all]"
```

`ffmpeg` on `PATH` (or `MEDIATRACE_FFMPEG`) enables merging video and audio, extracting audio, and stripping audio/video metadata.

For YouTube, recent yt-dlp versions also want a JavaScript runtime ([Deno](https://deno.com)) on `PATH`. Without one, downloads still work but some formats may be missing and yt-dlp prints a warning.

## File type detection

```python
from mediatrace import detect, FileTypeDetector

r = detect("holiday.jpg")
r.mime_type            # "image/png"  <- it's actually a PNG
r.category             # Category.IMAGE
r.extension_mismatch   # True
r.suggested_name       # "holiday.png"
```

- ~50 binary signatures, plus structured inspectors for ZIP/OOXML (docx/xlsx/pptx/epub/odt/jar/apk/whl…), OLE2 (doc/xls/ppt/msg), ISO-BMFF (mp4/mov/m4a/heic/avif/cr3), RIFF, EBML, Ogg, PE/ELF/Mach-O, tar, ISO, DMG/VHD trailers.
- Text sniffing: JSON/JSONL/ipynb/GeoJSON, XML/SVG/HTML/RSS/GPX/KML, CSV/TSV, shebang scripts, SRT/VTT/ASS, vCard/iCal, PEM, email.
- Reconciles content with the declared extension (`.jpeg`, `.m4a`, `.cbz`, `.tar.gz` and `.ts`-as-TypeScript are all handled correctly).
- Extensible: `detector.register_signature(b"MAGIC", "ext", "mime/type", "data")` or `detector.register(fn)`.
- Optional libmagic fallback: `FileTypeDetector(use_libmagic=True)`.

## Organizer

```python
from mediatrace import FileOrganizer, Rule, preset

org = FileOrganizer(
    "~/Sorted",
    rules=preset("media-timeline"),   # or your own Rule list
    dedupe=True, fix_extensions=True, date_source="exif",
)
plan = org.plan("~/Downloads")      # preview only; nothing is touched
report = org.execute(plan)          # moves files and writes an undo journal
FileOrganizer.undo(report.journal_path)
```

Rules match on category, extension, MIME glob, name regex/glob, size and age, or a custom predicate. Destinations are safe path templates:

```python
Rule("Photos/{year}/{month}", categories=["image"], priority=10)
Rule("Archive/{EXT}", older_than="6mo", min_size="100MB")
Rule("", action="skip", glob="*.iso")
Rule("{category}", rename="{date}_{stem}")
```

Template fields: `category, category_id, ext, EXT, mime_type, mime_subtype, year, month, month_name, day, date, quarter, size_bucket, parent, stem, name, initial`.

Other features: conflict strategies (`rename`/`skip`/`overwrite`), SHA-256 dedupe with a size/partial-hash pre-filter, idempotent in-place organizing, empty-dir cleanup, ignores OS litter and in-progress downloads, and parallel detection. Rules can be loaded from TOML/JSON with `load_rules("rules.toml")`. See [examples/rules.example.toml](examples/rules.example.toml).

To keep a folder organized, use `FolderWatcher(organizer, "~/Downloads").start()`. It polls with no extra dependencies and only picks up a file once it has stopped changing.

## Media downloader

```python
from mediatrace import MediaClient, DownloadOptions, CleanOptions

client = MediaClient(
    "downloads",
    options=DownloadOptions(quality="1080p", subtitles=True, thumbnail=True),
    clean=CleanOptions(image_format="jpg"),
    filename_template="{platform}/{uploader}/{date} {title} [{id}]",
)
info = client.info("https://youtu.be/...")          # metadata, formats, gallery entries
result = client.download("https://www.tiktok.com/@u/video/1?is_from_webapp=1")
result.primary                                       # final cleaned path
batch = client.download_many(urls, max_workers=4)    # errors collected, not raised
```

Every provider runs through the same pipeline: **normalize URL** (tracking params stripped per platform) → **fetch into staging** → **detect real type** → **fix extension** → **strip metadata** → **render safe name** → **conflict-safe move**.

Add your own platform by subclassing `MediaProvider` and calling `client.register_provider(MyProvider())`.

URL utilities: `normalize_url`, `detect_platform`, `extract_urls`.

> Only download content you have the right to download, and respect each platform's terms of service. mediatrace does not bypass DRM or access controls.

## CLI

```bash
mediatrace detect ./folder -r
mediatrace organize ~/Downloads -t ~/Sorted --preset downloads --dedupe --dry-run
mediatrace undo ~/Sorted/.mediatrace/journal-....json
mediatrace watch ~/Downloads -t ~/Sorted
mediatrace info "https://youtu.be/..." --formats
mediatrace fetch URL... -q 720p --subs --metadata -o downloads
mediatrace fetch -i links.txt -x --audio-format mp3
mediatrace clean-url "https://x.com/a/status/1?s=20&t=abc"
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

License: MIT
