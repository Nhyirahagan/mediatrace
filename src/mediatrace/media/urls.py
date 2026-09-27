"""URL normalization, tracking-parameter removal and platform detection."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from ..detection.categories import EXTENSIONS, MEDIA_CATEGORIES
from ..exceptions import InvalidURLError

#: platform id -> registrable domains (subdomains match too)
PLATFORM_DOMAINS: dict[str, tuple[str, ...]] = {
    "youtube": ("youtube.com", "youtu.be", "youtube-nocookie.com"),
    "tiktok": ("tiktok.com",),
    "instagram": ("instagram.com", "instagr.am"),
    "twitter": ("twitter.com", "x.com", "fxtwitter.com", "vxtwitter.com", "fixupx.com"),
    "facebook": ("facebook.com", "fb.watch", "fb.com"),
    "reddit": ("reddit.com", "redd.it"),
    "vimeo": ("vimeo.com",),
    "soundcloud": ("soundcloud.com",),
    "twitch": ("twitch.tv",),
    "pinterest": ("pinterest.com", "pin.it"),
    "tumblr": ("tumblr.com",),
    "dailymotion": ("dailymotion.com", "dai.ly"),
    "bilibili": ("bilibili.com", "b23.tv"),
    "threads": ("threads.net", "threads.com"),
    "bluesky": ("bsky.app",),
    "linkedin": ("linkedin.com",),
    "snapchat": ("snapchat.com",),
    "imgur": ("imgur.com",),
    "streamable": ("streamable.com",),
    "bandcamp": ("bandcamp.com",),
    "rumble": ("rumble.com",),
    "kick": ("kick.com",),
    "mixcloud": ("mixcloud.com",),
    "vk": ("vk.com", "vkvideo.ru"),
    "flickr": ("flickr.com", "flic.kr"),
}

#: Parameters that only track the sharer and never change the content.
GLOBAL_TRACKING_PARAMS = frozenset(
    {
        "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "yclid", "twclid", "ttclid",
        "mc_cid", "mc_eid", "_ga", "_gl", "_hsenc", "_hsmi", "mkt_tok", "igshid", "igsh",
        "ref_src", "ref_url", "spm", "share_id", "mibextid", "oly_enc_id", "oly_anon_id",
        "vero_id", "wickedid", "trk", "ncid", "sr_share", "rdt",
    }
)
TRACKING_PREFIXES = ("utm_", "pk_", "mtm_", "hsa_")

#: Extra per-platform tracking parameters. Note ``t`` is tracking on X/Twitter
#: but a timestamp on YouTube, so this has to be per platform.
PLATFORM_TRACKING_PARAMS: dict[str, frozenset[str]] = {
    "youtube": frozenset({"si", "feature", "pp", "ab_channel", "embeds_referring_euri", "source_ve_path"}),
    "tiktok": frozenset(
        {
            "is_from_webapp", "sender_device", "sender_web_id", "is_copy_url", "_r", "_t", "share_app_id",
            "share_link_id", "social_sharing", "source", "tt_from", "u_code", "preview_pb", "enter_method",
            "timestamp", "user_id", "sec_uid", "checksum", "share_item_id", "web_id", "lang", "_d",
        }
    ),
    "twitter": frozenset({"s", "t", "ref_src", "ref_url", "cn", "cxt"}),
    "instagram": frozenset({"igsh", "igshid", "utm_source", "hl"}),
    "facebook": frozenset({"mibextid", "sfnsn", "extid", "rdid", "share_url", "sfns"}),
    "reddit": frozenset({"share_id", "ref", "ref_source", "rdt", "sh"}),
    "threads": frozenset({"xmt", "slof", "igshid"}),
    "linkedin": frozenset({"trk", "lipi", "trackingid", "rcm", "midtoken", "midsig", "trkemail", "eid", "otptoken"}),
    "pinterest": frozenset({"invite_code", "sender", "sfo"}),
    "soundcloud": frozenset({"si", "utm_source", "utm_medium", "utm_campaign", "in"}),
    "vimeo": frozenset({"share", "fl", "fe"}),
}

_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_URL_IN_TEXT = re.compile(r"""(?:https?://|www\.)[^\s<>"'\])}]+""", re.IGNORECASE)


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix) :]
    return host


def url_extension(url: str) -> str:
    """Lowercase extension of the URL path (``"jpg"``), or ``""``."""
    return PurePosixPath(unquote(urlsplit(url).path)).suffix.lower().lstrip(".")


def is_direct_media_url(url: str) -> bool:
    entry = EXTENSIONS.get(url_extension(url))
    return entry is not None and entry[1] in MEDIA_CATEGORIES


def detect_platform(url: str) -> str:
    """Platform id for a URL: ``"youtube"``, ``"tiktok"``, ..., ``"direct"`` or ``"generic"``."""
    host = _host(url if _SCHEME.match(url) else "https://" + url)
    for platform, domains in PLATFORM_DOMAINS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return platform
    if is_direct_media_url(url):
        return "direct"
    return "generic"


def is_url(value: str) -> bool:
    try:
        normalize_url(value, strip_tracking=False)
    except InvalidURLError:
        return False
    return True


def _is_tracking(key: str, platform: str) -> bool:
    k = key.lower()
    return (
        k in GLOBAL_TRACKING_PARAMS
        or k.startswith(TRACKING_PREFIXES)
        or k in PLATFORM_TRACKING_PARAMS.get(platform, frozenset())
    )


def normalize_url(url: str, *, strip_tracking: bool = True) -> str:
    """Clean up a pasted URL.

    Adds a missing ``https://``, lowercases the host, drops credentials and
    fragments and (by default) removes tracking parameters such as ``utm_*``,
    ``fbclid``, ``si`` on YouTube or ``is_from_webapp`` on TikTok.
    """
    if not isinstance(url, str):
        raise InvalidURLError(f"URL must be a string, got {type(url).__name__}")
    url = url.strip().strip("<>\"'")
    if not url:
        raise InvalidURLError("empty URL")
    if not _SCHEME.match(url):
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        raise InvalidURLError(f"unsupported URL scheme: {parts.scheme!r}")
    host = (parts.hostname or "").lower().rstrip(".")
    if ":" in host:
        host = f"[{host}]"  # IPv6 literal
    if not host or ("." not in host and host != "localhost" and not host.startswith("[")):
        raise InvalidURLError(f"invalid host in URL: {url!r}")
    try:
        port = parts.port
    except ValueError as exc:
        raise InvalidURLError(f"invalid port in URL: {url!r}") from exc
    netloc = host if port is None else f"{host}:{port}"
    query = parts.query
    if strip_tracking and query:
        platform = detect_platform(f"https://{host}{parts.path}")
        kept = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if not _is_tracking(k, platform)]
        query = urlencode(kept, doseq=True)
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", query, ""))


def extract_urls(text: str) -> list[str]:
    """Find http(s) URLs in free text (chat messages, notes), de-duplicated, in order."""
    seen: dict[str, None] = {}
    for raw in _URL_IN_TEXT.findall(text):
        candidate = raw.rstrip(".,;:!?")
        try:
            seen.setdefault(normalize_url(candidate), None)
        except InvalidURLError:
            continue
    return list(seen)
