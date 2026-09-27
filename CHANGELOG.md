# Changelog

## [Unreleased]

### Fixed
- UTF-16 LE text (BOM `FF FE`) was misdetected as MPEG Layer I audio. MPEG frame headers with an invalid bitrate or sample-rate index are now rejected.

### Added
- GitHub Actions CI running the test suite on Linux, Windows and macOS (Python 3.11–3.13).

## [0.1.0] - 2026-09-27

### Added
- Content-based file type detection (magic bytes, container inspection, text heuristics).
- Rule-based `FileOrganizer` with presets, TOML/JSON rules, dedupe, conflict strategies, undo journal and `FolderWatcher`.
- `MediaClient` with yt-dlp and direct HTTP providers, URL cleaning, extension fixing, metadata stripping and template-based naming.
- `mediatrace` CLI: `detect`, `organize`, `undo`, `watch`, `info`, `fetch`, `clean-url`.
