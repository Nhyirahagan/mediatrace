from __future__ import annotations

import http.server
import json
import threading
from datetime import date
from pathlib import Path

import pytest

from mediatrace import (
    CleanOptions,
    DirectProvider,
    DownloadOptions,
    MediaClient,
    MediaInfo,
    MediaProvider,
    detect_platform,
    extract_urls,
    normalize_url,
)
from mediatrace.exceptions import DownloadError, InvalidURLError, TemplateError, UnsupportedURLError
from mediatrace.media import DownloadedFile, MediaCleaner, YtDlpProvider
from mediatrace.media.providers import info_from_ytdlp

from .conftest import JPEG, MP4, PNG, WEBP

# ----------------------------------------------------------------------- urls


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://youtu.be/abc123?si=XYZ", "https://youtu.be/abc123"),
        ("https://www.youtube.com/watch?v=abc&t=42&feature=share", "https://www.youtube.com/watch?v=abc&t=42"),
        ("https://x.com/user/status/1?s=20&t=abc", "https://x.com/user/status/1"),
        ("https://www.tiktok.com/@u/video/1?is_from_webapp=1&sender_device=pc", "https://www.tiktok.com/@u/video/1"),
        ("https://www.instagram.com/p/XYZ/?igsh=abc&img_index=2", "https://www.instagram.com/p/XYZ/?img_index=2"),
        ("instagram.com/reel/ABC", "https://instagram.com/reel/ABC"),
        ("  <HTTPS://Example.COM/a?utm_source=x&id=5#frag>  ", "https://example.com/a?id=5"),
        ("https://user:pass@example.com:8080/v.mp4?fbclid=1", "https://example.com:8080/v.mp4"),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


def test_normalize_keeps_query_when_asked():
    assert "utm_source" in normalize_url("https://example.com/?utm_source=a", strip_tracking=False)


@pytest.mark.parametrize("bad", ["", "ftp://example.com/x", "https://", "notaurl", "javascript:alert(1)"])
def test_normalize_rejects(bad):
    with pytest.raises(InvalidURLError):
        normalize_url(bad)


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://m.youtube.com/watch?v=1", "youtube"),
        ("https://music.youtube.com/watch?v=1", "youtube"),
        ("https://vm.tiktok.com/abc/", "tiktok"),
        ("https://x.com/a/status/1", "twitter"),
        ("https://old.reddit.com/r/x", "reddit"),
        ("https://v.redd.it/abc", "reddit"),
        ("https://cdn.example.com/a/b/photo.JPG", "direct"),
        ("https://example.com/page", "generic"),
        ("https://notyoutube.com/watch", "generic"),
    ],
)
def test_detect_platform(url, platform):
    assert detect_platform(url) == platform


def test_extract_urls():
    text = "look: https://youtu.be/a?si=1, and www.example.com/x.png. dup https://youtu.be/a"
    assert extract_urls(text) == ["https://youtu.be/a", "https://www.example.com/x.png"]


# ------------------------------------------------------------------ options


@pytest.mark.parametrize(
    ("quality", "height"), [("best", None), ("720p", 720), ("1080", 1080), (480, 480), ("4k", 2160), ("hd", 720)]
)
def test_quality_parsing(quality, height):
    assert DownloadOptions(quality=quality).max_height == height


def test_format_selector():
    sel = YtDlpProvider.format_selector
    assert sel(DownloadOptions(), True) == "bestvideo*+bestaudio/best/best"
    assert sel(DownloadOptions(quality="720p"), True) == "bestvideo*[height<=720]+bestaudio/best[height<=720]/best"
    assert sel(DownloadOptions(quality="720p"), False) == "best[height<=720]/best"
    assert sel(DownloadOptions(audio_only=True), True) == "bestaudio/best"
    assert sel(DownloadOptions(quality="audio"), False) == "bestaudio/best"
    assert sel(DownloadOptions(format="137+140"), True) == "137+140"


def test_info_from_ytdlp():
    raw = {
        "_type": "playlist",
        "id": "PL1",
        "title": "Gallery",
        "webpage_url": "https://www.instagram.com/p/XYZ/",
        "uploader": "someone",
        "entries": [
            {"id": "a", "title": "one", "ext": "jpg", "upload_date": "20240131"},
            {"id": "b", "title": "two", "ext": "mp4", "vcodec": "h264", "acodec": "aac",
             "formats": [{"format_id": "1", "ext": "mp4", "height": 720, "vcodec": "h264", "acodec": "none"}]},
            None,
        ],
    }
    info = info_from_ytdlp(raw)
    assert info.platform == "instagram"
    assert info.media_type == "collection"
    assert [e.media_type for e in info.entries] == ["image", "video"]
    assert info.entries[0].upload_date == date(2024, 1, 31)
    fmt = info.entries[1].formats[0]
    assert fmt.has_video and not fmt.has_audio and fmt.resolution == "720p"
    assert json.dumps(info.to_dict())


def test_ytdlp_collect_maps_sidecars(tmp_path):
    info = MediaInfo(id="abc", url="u", platform="youtube", entries=[])
    for name in ("abc.mp4", "abc.en.vtt", "abc.thumbnail.webp", "abc.mp4.part"):
        (tmp_path / name).write_bytes(b"x")
    files = YtDlpProvider._collect(tmp_path, info, {"abc": "abc"})
    kinds = {(f.path.name, f.kind, f.label) for f in files}
    assert kinds == {("abc.mp4", "media", ""), ("abc.en.vtt", "subtitle", "en"), ("abc.thumbnail.webp", "thumbnail", "thumb")}


# ------------------------------------------------------------------ cleaning


def test_cleaner_fixes_extension(tmp_path):
    path = tmp_path / "x.jpg"
    path.write_bytes(WEBP)
    result = MediaCleaner(CleanOptions(strip_metadata=False)).clean(path)
    assert result.path.name == "x.webp"
    assert any(s.startswith("extension:") for s in result.steps)


def test_cleaner_rejects_html(tmp_path):
    path = tmp_path / "x.mp4"
    path.write_text("<!DOCTYPE html><html><body>Log in to continue</body></html>")
    with pytest.raises(DownloadError, match="HTML page"):
        MediaCleaner().clean(path)


def test_cleaner_strips_exif(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    path = tmp_path / "photo.jpg"
    img = Image.new("RGB", (8, 4), "red")
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"  # Make
    exif[0x0112] = 6  # Orientation: rotate 90 CW
    img.save(path, exif=exif)
    result = MediaCleaner().clean(path)
    assert "stripped-image-metadata" in result.steps
    with Image.open(result.path) as out:
        assert not dict(out.getexif())
        assert out.size == (4, 8)  # orientation baked into pixels


def test_cleaner_converts_images(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    path = tmp_path / "pic.png"
    Image.new("RGBA", (4, 4), (0, 0, 255, 128)).save(path)
    result = MediaCleaner(CleanOptions(image_format="jpg")).clean(path)
    assert result.path.suffix == ".jpg"
    assert not path.exists()


# ------------------------------------------------------------------- client


class FakeProvider(MediaProvider):
    """Writes canned files into the staging directory."""

    name = "fake"

    def __init__(self, payloads: dict[str, bytes], info: MediaInfo, labels: dict[str, tuple[str, str]] | None = None):
        self.payloads = payloads
        self.info = info
        self.labels = labels or {}
        self.calls: list[str] = []

    def supports(self, url: str) -> bool:
        return "fake.test" in url

    def extract_info(self, url, options):
        return self.info

    def download(self, url, staging_dir: Path, options):
        self.calls.append(url)
        files = []
        entries = {e.id: e for e in self.info.walk()}
        for name, data in self.payloads.items():
            path = staging_dir / name
            path.write_bytes(data)
            entry_id = name.split(".", 1)[0]
            kind, label = self.labels.get(name, ("media", ""))
            files.append(DownloadedFile(path, entries.get(entry_id, self.info), kind, label))  # type: ignore[arg-type]
        return self.info, files


def single_info() -> MediaInfo:
    return MediaInfo(
        id="v1", url="https://fake.test/v1", platform="fake", title="My: Video / Title?",
        uploader="Some One", upload_date=date(2024, 5, 6), media_type="video",
    )


def test_client_pipeline(tmp_path):
    provider = FakeProvider({"v1.bin": MP4, "v1.en.vtt": b"WEBVTT\n\n", "v1.thumbnail.jpg": PNG}, single_info(),
                            {"v1.en.vtt": ("subtitle", "en"), "v1.thumbnail.jpg": ("thumbnail", "thumb")})
    client = MediaClient(tmp_path, providers=[provider])
    result = client.download("https://fake.test/v1?utm_source=spam", write_metadata=True)

    assert provider.calls == ["https://fake.test/v1"]
    base = tmp_path / "fake" / "Some One"
    names = {f.path.relative_to(base).as_posix() for f in result.files}
    assert names == {
        "My_ Video - Title_ [v1].mp4",  # extension fixed from .bin, name sanitized
        "My_ Video - Title_ [v1].en.vtt",
        "My_ Video - Title_ [v1].thumb.png",  # thumbnail was really a PNG
        "My_ Video - Title_ [v1].info.json",
    }
    assert result.primary == base / "My_ Video - Title_ [v1].mp4"
    meta = json.loads((base / "My_ Video - Title_ [v1].info.json").read_text(encoding="utf-8"))
    assert meta["upload_date"] == "2024-05-06"
    assert not any(p.name.startswith(".mediatrace-staging") for p in tmp_path.iterdir())


def test_client_collection_template(tmp_path):
    info = MediaInfo(
        id="post", url="https://fake.test/p", platform="fake", title="Album", uploader="u", media_type="collection",
        entries=[
            MediaInfo(id="a", url="", platform="fake", title="", media_type="image"),
            MediaInfo(id="b", url="", platform="fake", title="", media_type="image"),
        ],
    )
    provider = FakeProvider({"a.jpg": JPEG, "b.jpg": PNG}, info)
    client = MediaClient(tmp_path, providers=[provider], filename_template="{collection}/{index:02d}_{id}")
    result = client.download("https://fake.test/p")
    assert sorted(f.path.relative_to(tmp_path).as_posix() for f in result.files) == [
        "Album/01_a.jpg",
        "Album/02_b.png",
    ]


def test_client_conflicts(tmp_path):
    provider = FakeProvider({"v1.mp4": MP4}, single_info())
    client = MediaClient(tmp_path, providers=[provider], filename_template="{id}")
    first = client.download("https://fake.test/v1").primary
    second = client.download("https://fake.test/v1").primary
    assert first.name == "v1.mp4" and second.name == "v1 (1).mp4"

    skip = MediaClient(tmp_path, providers=[provider], filename_template="{id}", conflict="skip")
    result = skip.download("https://fake.test/v1")
    assert result.files[0].skipped and result.primary == first


def test_client_rejects_html_media(tmp_path):
    provider = FakeProvider({"v1.mp4": b"<html><body>Please log in</body></html>"}, single_info())
    with pytest.raises(DownloadError):
        MediaClient(tmp_path, providers=[provider]).download("https://fake.test/v1")
    assert list(tmp_path.iterdir()) == []


def test_client_no_clean_keeps_bytes(tmp_path):
    provider = FakeProvider({"v1.bin": MP4}, single_info())
    result = MediaClient(tmp_path, providers=[provider], clean=False, filename_template="{id}").download(
        "https://fake.test/v1"
    )
    assert result.primary.name == "v1.bin"


def test_client_unsupported_and_bad_template(tmp_path):
    client = MediaClient(tmp_path, providers=[FakeProvider({}, single_info())])
    with pytest.raises(UnsupportedURLError):
        client.download("https://elsewhere.example/page")
    with pytest.raises(TemplateError):
        MediaClient(tmp_path, filename_template="{nope}")
    with pytest.raises(TypeError):
        client.download("https://fake.test/v1", not_an_option=True)


def test_download_many_collects_errors(tmp_path):
    client = MediaClient(tmp_path, providers=[FakeProvider({"v1.mp4": MP4}, single_info())])
    batch = client.download_many(["https://fake.test/v1", "https://nothing.example/x", "https://fake.test/v1"])
    assert len(batch.results) == 1
    assert list(batch.errors) == ["https://nothing.example/x"]


def test_info_passthrough(tmp_path):
    client = MediaClient(tmp_path, providers=[FakeProvider({}, single_info())])
    assert client.info("fake.test/v1").title == "My: Video / Title?"


# ------------------------------------------------------------ direct provider


class _Handler(http.server.BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, str, bytes]] = {}

    def _send(self, body: bool) -> None:
        status, ctype, data = self.routes.get(self.path.split("?")[0], (404, "text/plain", b"nope"))
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if body:
            self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        self._send(True)

    def do_HEAD(self):  # noqa: N802
        self._send(False)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    _Handler.routes = {
        "/media/photo.jpg": (200, "image/jpeg", JPEG),
        "/media/lying.jpg": (200, "image/jpeg", PNG),
        "/media/blocked.mp4": (200, "text/html", b"<html>login</html>"),
        "/media/huge.mp4": (200, "video/mp4", MP4 * 100),
    }
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_direct_provider_supports():
    p = DirectProvider()
    assert p.supports("https://a.example/x/y.MP4?sig=1")
    assert not p.supports("https://a.example/page.html")
    assert not p.supports("https://a.example/doc.pdf")


def test_direct_download_and_info(server, tmp_path):
    client = MediaClient(tmp_path, providers=[DirectProvider()], filename_template="{platform}/{title}")
    info = client.info(f"{server}/media/photo.jpg")
    assert info.filesize == len(JPEG) and info.media_type == "image"

    events = []
    result = client.download(f"{server}/media/photo.jpg", progress=events.append)
    assert result.primary == tmp_path / "direct" / "photo.jpg"
    assert result.primary.read_bytes()[:3] == b"\xff\xd8\xff"
    assert events and events[-1].status == "finished"

    lying = client.download(f"{server}/media/lying.jpg")
    assert lying.primary.name == "lying.png"


def test_direct_download_errors(server, tmp_path):
    client = MediaClient(tmp_path, providers=[DirectProvider()], options=DownloadOptions(retries=0))
    with pytest.raises(DownloadError, match="HTML"):
        client.download(f"{server}/media/blocked.mp4")
    with pytest.raises(DownloadError, match="limit"):
        client.download(f"{server}/media/huge.mp4", max_filesize=100)
    with pytest.raises(DownloadError, match="404"):
        client.download(f"{server}/media/missing.mp4")
