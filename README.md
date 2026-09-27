# mediatrace

[![CI](https://github.com/Nhyirahagan/mediatrace/actions/workflows/ci.yml/badge.svg)](https://github.com/Nhyirahagan/mediatrace/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)
![Dependencies](https://img.shields.io/badge/core%20dependencies-0-brightgreen)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**Know what your files really are, put them where they belong, and download media from anywhere as clean, correctly named files.**

mediatrace is a Python library and command-line tool with two parts that share one detection engine:

| | What it does |
|---|---|
| **Smart file organizer** | Sorts messy folders by what files *actually contain* (magic bytes, container structure, text heuristics), not by what their extension claims. Rule-based, previewable, reversible. |
| **Unified media downloader** | One API for YouTube, TikTok, Instagram, X, Reddit, Vimeo, SoundCloud, direct links and 1,000+ more sites via yt-dlp. Every download is verified, has its extension fixed, is stripped of EXIF/GPS and container metadata, and is given a safe, predictable name. |

The core needs **nothing but the Python standard library**. Extra features switch on automatically when optional tools are installed.

---

## Contents

- [Why mediatrace](#why-mediatrace)
- [Installation](#installation)
- [Quick start](#quick-start)
- [File type detection](#file-type-detection)
- [File organizer](#file-organizer)
- [Media downloader](#media-downloader)
- [Command-line reference](#command-line-reference)
- [Extending mediatrace](#extending-mediatrace)
- [Error handling](#error-handling)
- [Development](#development)
- [Responsible use](#responsible-use)
- [License](#license)

---

## Why mediatrace

- **Extensions lie.** A `photo.jpg` from the web is often a WebP or PNG, and a "video" link can return an HTML login page. mediatrace looks at the bytes, reports mismatches, and can fix them.
- **Organizing should be safe.** Every run can be previewed first (`--dry-run`), writes an undo journal, never overwrites unless you ask it to, and can be reversed with one command.
- **Downloads should be clean.** Shared links carry tracking parameters and downloaded files carry location data and uploader metadata. mediatrace strips both.
- **One pipeline, many sources.** Every platform goes through the same steps, so results are consistent whether the file came from YouTube or a direct URL.

---

## Installation

Requires **Python 3.11+**.

```bash
pip install "git+https://github.com/Nhyirahagan/mediatrace.git"
```

Optional extras add features on top of the zero-dependency core:

| Extra | Adds | Enables |
|---|---|---|
| *(none)* | – | Detection, organizer, watcher, direct-URL downloads |
| `[media]` | yt-dlp | Downloads from YouTube, TikTok, Instagram, X and other platforms |
| `[images]` | Pillow | EXIF dates for sorting, image metadata stripping, image conversion |
| `[magic]` | python-magic | libmagic fallback for rare formats |
| `[all]` | yt-dlp + Pillow | Everything above except libmagic |

```bash
pip install "mediatrace[all] @ git+https://github.com/Nhyirahagan/mediatrace.git"
```

**External tools (optional):**

- **ffmpeg** on `PATH` (or set `MEDIATRACE_FFMPEG=/path/to/ffmpeg`) merges separate video and audio streams, extracts audio (`-x`) and strips audio/video metadata.
- **Deno** on `PATH` is recommended for YouTube. Recent yt-dlp versions use a JavaScript runtime to unlock all formats. Without one, downloads still work but some formats may be missing and yt-dlp prints a warning.

---

## Quick start

**Command line:**

```bash
# What is this file, really?
mediatrace detect mystery.bin

# Preview how a Downloads folder would be sorted, then do it
mediatrace organize ~/Downloads -t ~/Sorted --preset downloads --dry-run
mediatrace organize ~/Downloads -t ~/Sorted --preset downloads

# Changed your mind? Reverse it
mediatrace undo ~/Sorted/.mediatrace/journal-20260927-152209-247829.json

# Download a video at 720p with subtitles
mediatrace fetch "https://www.youtube.com/watch?v=jNQXAC9IVRw" -q 720p --subs
```

**Python:**

```python
from mediatrace import detect, FileOrganizer, MediaClient, preset

detect("holiday.jpg").mime_type                      # "image/png"

org = FileOrganizer("~/Sorted", rules=preset("media-timeline"))
org.organize("~/Downloads", dry_run=True)

MediaClient("downloads").download("https://youtu.be/jNQXAC9IVRw")
```

---

## File type detection

```python
from mediatrace import detect, FileTypeDetector

r = detect("holiday.jpg")
r.mime_type            # "image/png"   <- it is actually a PNG
r.extension            # "png"
r.category             # Category.IMAGE
r.description          # "PNG image"
r.confidence           # 0.99
r.method               # "signature"
r.extension_mismatch   # True
r.suggested_name       # "holiday.png"

# Bytes work too (e.g. an HTTP response body)
FileTypeDetector().detect_bytes(data, "download.bin")
```

### How it works

Detection runs in layers, and the most confident answer wins:

1. **Binary signatures:** about 50 magic-byte patterns (JPEG, PNG, GIF, WebP, PDF, ZIP, 7z, RAR, gzip, FLAC, MP3, fonts, SQLite and more).
2. **Container inspection:** looks *inside* formats that share a wrapper:
   - **ZIP:** docx / xlsx / pptx, ODT / ODS / ODP, EPUB, JAR, APK, wheel, CBZ, and more
   - **OLE2:** legacy doc / xls / ppt / msg
   - **ISO-BMFF:** mp4, mov, m4a / m4b, HEIC, AVIF, Canon CR3
   - **RIFF:** WAV, AVI, WebP; **EBML:** MKV, WebM; **Ogg:** Vorbis, Opus, Theora
   - **Executables:** PE, ELF, Mach-O; **Disk images:** tar, ISO, DMG, VHD
3. **Text sniffing:** JSON / JSONL / Jupyter notebooks / GeoJSON, XML / SVG / HTML / RSS / GPX / KML, CSV / TSV, shebang scripts, SRT / VTT / ASS subtitles, vCard / iCalendar, PEM keys, email messages. Handles UTF-8/16/32 byte-order marks.
4. **Extension reconciliation:** content and the declared extension are combined sensibly. `.jpeg` for a JPEG is not a mismatch, `.tar.gz` is preserved, and `.ts` stays TypeScript unless the bytes really are an MPEG transport stream.
5. **libmagic fallback** (optional): `FileTypeDetector(use_libmagic=True)`.

### Categories

Every result belongs to one of these categories, which the organizer uses for folders:

`image` · `video` · `audio` · `document` · `spreadsheet` · `presentation` · `ebook` · `archive` · `code` · `data` · `database` · `font` · `executable` · `disk_image` · `design` · `model_3d` · `subtitle` · `text` · `other`

---

## File organizer

```python
from mediatrace import FileOrganizer, Rule, preset

org = FileOrganizer(
    "~/Sorted",                       # target root (None = organize in place)
    rules=preset("media-timeline"),
    dedupe=True,                      # SHA-256 duplicate detection
    fix_extensions=True,              # rename files whose extension lies
    date_source="exif",               # "modified" | "created" | "exif"
)

plan = org.plan("~/Downloads")        # preview only; nothing is touched
for op in plan.operations:
    print(op)

report = org.execute(plan)            # move files, write an undo journal
print(report.journal_path)

FileOrganizer.undo(report.journal_path)
```

### Presets

| Preset | Result |
|---|---|
| `category` | `Images/`, `Videos/`, `Documents/`, ... |
| `category-date` | `Images/2026/09/`, `Documents/2026/09/`, ... |
| `date` | `2026/09/` |
| `extension` | `PDF/`, `JPG/`, `MP4/`, ... |
| `media-timeline` | `Photos/{year}/{month}`, `Videos/{year}/{month}`, everything else by category |
| `downloads` | `Installers/`, `Archives/`, `Screenshots/{year}-{month}`, everything else by category |

### Rules

Rules are tried from highest `priority` to lowest, and **the first match wins**. A rule matches only when *all* of its conditions match.

```python
Rule("Photos/{year}/{month}", categories=["image"], priority=10)
Rule("Archive/{EXT}", older_than="6mo", min_size="100MB")
Rule("Screenshots", name_pattern=r"^screen ?shot", priority=20)
Rule("", action="skip", glob="*.iso")                    # leave these alone
Rule("{category}", rename="{date}_{stem}")               # rename while moving
Rule("Big", predicate=lambda f: f.size > 2**30)          # custom Python check
```

| Condition | Example |
|---|---|
| `categories` | `["image", "video"]` |
| `extensions` | `["pdf", "epub"]` |
| `mime_types` | `["image/*", "application/pdf"]` (glob patterns) |
| `name_pattern` | regex on the file name |
| `glob` | `"*.iso"` |
| `min_size` / `max_size` | `"100MB"`, `"2GB"`, or bytes |
| `older_than` / `newer_than` | `"12h"`, `"30d"`, `"2w"`, `"6mo"`, `"1y"`, a number of days, or a `timedelta` |
| `predicate` | any `Callable[[FileInfo], bool]` |

Actions: `move` (default), `copy`, `skip`.

**Template fields** for destinations and `rename`:

`category` · `category_id` · `ext` · `EXT` · `mime_type` · `mime_subtype` · `year` · `month` · `month_name` · `day` · `date` · `quarter` · `size_bucket` · `parent` · `stem` · `name` · `initial`

Templates are sanitized: path traversal (`..`), reserved Windows names and illegal characters are rejected or replaced.

### Rules from a file

```toml
# rules.toml
[[rules]]
name = "screenshots"
categories = ["image"]
name_pattern = "^(screenshot|screen shot)"
destination = "Screenshots/{year}-{month}"
priority = 30

[[rules]]
name = "everything-else"
destination = "{category}"
```

```python
from mediatrace import load_rules
org = FileOrganizer("~/Sorted", rules=load_rules("rules.toml"))
```

```bash
mediatrace organize ~/Downloads --rules rules.toml
```

A fuller example is in [examples/rules.example.toml](examples/rules.example.toml). JSON works the same way.

### Safety features

- **Dry run.** `plan()` / `--dry-run` shows every operation before anything changes.
- **Undo journal.** Each run writes `.mediatrace/journal-*.json`. `undo` moves files back and removes copies.
- **Conflicts.** `rename` (default, adds ` (1)`), `skip`, or `overwrite`.
- **Deduplication.** Files are compared by size first, then a partial hash, then a full SHA-256. Duplicates are left in place (`skip`) or moved to `_Duplicates/` (`move`).
- **Idempotent.** Running twice does nothing the second time; files already in the right place stay put.
- **Ignores litter.** OS files (`Thumbs.db`, `.DS_Store`, `desktop.ini`) and in-progress downloads (`*.part`, `*.crdownload`, ...) are skipped.
- **Cleanup.** `cleanup_empty_dirs=True` / `--cleanup` removes folders left empty.
- **Parallel.** Detection runs across threads for large folders.

### Keeping a folder organized

```python
from mediatrace import FolderWatcher

watcher = FolderWatcher(org, "~/Downloads", interval=5, settle=3)
watcher.start()        # background thread
...
watcher.stop()
```

The watcher polls, so it needs no extra dependencies. It only moves a file after its size and modification time have stopped changing for `settle` seconds, so half-finished downloads are left alone.

---

## Media downloader

```python
from mediatrace import MediaClient, DownloadOptions, CleanOptions

client = MediaClient(
    "downloads",
    options=DownloadOptions(quality="1080p", subtitles=True, thumbnail=True),
    clean=CleanOptions(image_format="jpg"),
    filename_template="{platform}/{uploader}/{date} {title} [{id}]",
)

info = client.info("https://youtu.be/jNQXAC9IVRw")    # metadata only
info.title, info.uploader, info.duration, info.formats

result = client.download("https://www.tiktok.com/@user/video/123?is_from_webapp=1")
result.primary              # Path to the final, cleaned media file
result.files                # media + subtitles + thumbnail + metadata

batch = client.download_many(urls, max_workers=4)     # failures collected, not raised
```

### The pipeline

Every download, from any provider, goes through the same steps:

```
normalize URL  →  fetch into staging  →  detect real type  →  fix extension
      →  strip metadata  →  render safe name  →  conflict-safe move
```

- **Normalize URL:** removes tracking parameters (`utm_*`, `fbclid`, `si`, `igsh`, X's `s`/`t`, ...) per platform, so YouTube's `t=` timestamp survives but X's `t=` tracker does not.
- **Detect and verify:** an HTML error or login page saved as `video.mp4` is rejected instead of silently kept.
- **Fix extension:** a `.jpg` that is really WebP becomes `.webp` (or is converted with `image_format`).
- **Strip metadata:** EXIF/GPS is removed from images (Pillow). Container tags are removed from audio/video with a lossless stream copy (ffmpeg).
- **Safe names:** titles are sanitized for every operating system, truncated sensibly, and optionally made ASCII-only.

### Supported platforms

Platform detection and URL cleaning are built in for:

YouTube · TikTok · Instagram · X / Twitter · Facebook · Reddit · Vimeo · SoundCloud · Twitch · Pinterest · Tumblr · Dailymotion · Bilibili · Threads · Bluesky · LinkedIn · Snapchat · Imgur · Streamable · Bandcamp · Rumble · Kick · Mixcloud · VK · Flickr

With `[media]` installed, any other site yt-dlp supports also works. Direct links to files (`.mp4`, `.jpg`, `.pdf`, ...) work with no extras at all.

### Download options

| `DownloadOptions` field | Default | Meaning |
|---|---|---|
| `quality` | `"best"` | `"best"`, `"worst"`, `"audio"`, `"1080p"`, `720`, `"4k"` |
| `audio_only` / `audio_format` | `False` / `"mp3"` | Extract audio (needs ffmpeg) |
| `video_format` | `"mp4"` | Container to merge/remux into |
| `format` | `None` | Raw yt-dlp format selector, for full control |
| `playlist` / `playlist_items` | `False` / `None` | Expand playlists and channels, e.g. `"1-5,8"` |
| `subtitles` / `subtitle_langs` | `False` / `("en",)` | Download subtitles |
| `thumbnail` | `False` | Save the thumbnail |
| `write_metadata` | `False` | Write normalized `<name>.info.json` |
| `cookies_file` | `None` | Netscape `cookies.txt` for content you have access to |
| `proxy`, `rate_limit`, `max_filesize`, `timeout` | | Network controls (`"2M"`, `"500MB"`, seconds) |

Any field can also be overridden per call: `client.download(url, quality="720p", subtitles=True)`.

| `CleanOptions` field | Default | Meaning |
|---|---|---|
| `fix_extensions` | `True` | Correct wrong extensions |
| `strip_metadata` | `True` | Remove EXIF/GPS from images |
| `strip_av_metadata` | `True` | Remove audio/video container tags |
| `image_format` / `image_quality` | `None` / `92` | Convert images to `jpg`, `png` or `webp` |
| `reject_html` | `True` | Fail when "media" is actually a web page |
| `sanitize_names` / `ascii_names` / `max_name_length` | `True` / `False` / `150` | File-name safety |

Pass `clean=False` to keep files exactly as downloaded.

### Filename templates

Default: `{platform}/{uploader}/{title} [{id}]`. The extension is added automatically.

Fields: `id` · `title` · `uploader` · `uploader_id` · `platform` · `date` · `year` · `month` · `day` · `media_type` · `index` · `collection` · `collection_id` · `height`

### URL utilities

```python
from mediatrace import normalize_url, detect_platform, extract_urls

normalize_url("https://x.com/a/status/1?s=20&t=abc")   # "https://x.com/a/status/1"
detect_platform("https://youtu.be/abc")                 # "youtube"
extract_urls("see https://youtu.be/a and https://vimeo.com/1")
```

---

## Command-line reference

```
mediatrace [-v] <command> ...
```

| Command | Purpose |
|---|---|
| `detect PATH... [-r] [--libmagic] [--json]` | Identify file types by content and flag lying extensions |
| `organize SOURCE [options]` | Sort a directory |
| `undo JOURNAL` | Reverse an organize run |
| `watch SOURCE [options] [--interval N] [--settle N]` | Keep a directory organized |
| `info URL [--formats] [--playlist] [--json]` | Show media metadata without downloading |
| `fetch URL... [options]` | Download and clean media |
| `clean-url URL...` | Strip tracking parameters and identify the platform |

**`organize` / `watch` options:**
`-t/--target DIR` · `--preset NAME` or `--rules FILE` · `--copy` · `-r/--recursive` · `--include-hidden` · `--conflict {rename,skip,overwrite}` · `--date-source {modified,created,exif}` · `--dedupe [{skip,move}]` · `--fix-extensions` · `--cleanup` · `--no-journal` · `-n/--dry-run` · `--json`

**`fetch` options:**
`-i/--input FILE` (one URL per line, `-` for stdin) · `-o/--output DIR` · `-q/--quality` · `-x/--audio-only` · `--audio-format` · `--playlist` · `--subs` · `--thumbnail` · `--metadata` · `--template` · `--conflict` · `--no-clean` · `--keep-metadata` · `--image-format {jpg,png,webp}` · `--ascii` · `--cookies FILE` · `--proxy` · `--rate-limit 2M` · `--max-filesize 500MB` · `-w/--workers N` · `--json`

**Examples:**

```bash
mediatrace detect ./folder -r --json > report.json
mediatrace organize ~/Pictures --preset media-timeline --date-source exif --dedupe move
mediatrace watch ~/Downloads -t ~/Sorted --preset downloads --interval 10
mediatrace info "https://youtu.be/jNQXAC9IVRw" --formats
mediatrace fetch -i links.txt -x --audio-format mp3 -w 4
mediatrace fetch URL --template "{uploader}/{date} {title}" --ascii
mediatrace clean-url "https://x.com/a/status/1?s=20&t=abc"
```

`python -m mediatrace` works the same as `mediatrace`.

---

## Extending mediatrace

**Teach the detector a new format:**

```python
from mediatrace import FileTypeDetector

det = FileTypeDetector()
det.register_signature(b"MYFMT\x00", "myf", "application/x-myformat", "data")

def sniff(probe):                       # full control over matching
    ...
det.register(sniff)
```

**Add a download source:**

```python
from mediatrace import MediaClient, MediaProvider

class MyProvider(MediaProvider):
    name = "myplatform"

    def supports(self, url):
        return "myplatform.example" in url

    def extract_info(self, url, options):
        ...                             # return a MediaInfo

    def download(self, url, staging_dir, options):
        ...                             # save into staging_dir, return (MediaInfo, [DownloadedFile])

client = MediaClient("downloads")
client.register_provider(MyProvider())
```

A provider only fetches bytes into a staging directory. The client handles detection, cleaning, naming and moving, so every provider gets the full pipeline for free.

---

## Error handling

All exceptions inherit from `MediaTraceError`:

| Exception | Raised when |
|---|---|
| `DetectionError` | A file cannot be read for detection |
| `OrganizerError` / `RuleError` / `TemplateError` / `JournalError` | Invalid configuration, rules, templates or undo journals |
| `MediaError` / `DownloadError` | A download fails or returns something that is not media |
| `InvalidURLError` / `UnsupportedURLError` | A URL is malformed or no provider can handle it |
| `ProviderUnavailableError` | The provider needs an optional dependency that is not installed |

`FileOrganizer.execute()` and `MediaClient.download_many()` never raise for individual items. Failures are recorded in the report so one bad file does not stop the rest.

Logging uses the standard `logging` module under the `mediatrace` logger. Use `mediatrace -v` for verbose CLI output.

---

## Development

```bash
git clone https://github.com/Nhyirahagan/mediatrace.git
cd mediatrace
python -m venv .venv
.venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

```
src/mediatrace/
├── detection/        # signatures, container inspectors, text sniffing, categories
├── organizer/        # rules, presets, FileOrganizer, FolderWatcher
├── media/            # MediaClient, URL cleaning, cleaning pipeline, providers/
├── naming.py         # safe filename and path templates
├── imaging.py        # EXIF dates, metadata stripping, conversion (Pillow)
└── cli.py            # the `mediatrace` command
```

CI runs the test suite on Linux, Windows and macOS with Python 3.11, 3.12 and 3.13. See [CHANGELOG.md](CHANGELOG.md) for release notes.

---

## Responsible use

Only download content you have the right to download, and respect each platform's terms of service and the rights of creators. mediatrace does not bypass DRM, paywalls or access controls. The `cookies` option exists only so you can access content your own account is already allowed to see.

---

## License

[MIT](LICENSE) © Nhyirahagan
