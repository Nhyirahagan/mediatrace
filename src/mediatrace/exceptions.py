"""Exception hierarchy for mediatrace.

Every error raised deliberately by the library derives from :class:`MediaTraceError`,
so applications can catch one type at their boundary.
"""

from __future__ import annotations


class MediaTraceError(Exception):
    """Base class for all mediatrace errors."""


class DetectionError(MediaTraceError):
    """A file could not be read or inspected."""


class TemplateError(MediaTraceError, ValueError):
    """A path/name template is malformed or references an unknown field."""


class OrganizerError(MediaTraceError):
    """The organizer could not plan or execute an operation."""


class RuleError(OrganizerError, ValueError):
    """A sorting rule is invalid."""


class JournalError(OrganizerError):
    """An undo journal is missing or malformed."""


class MediaError(MediaTraceError):
    """Base class for media fetching errors."""


class InvalidURLError(MediaError, ValueError):
    """The input is not a usable http(s) URL."""


class UnsupportedURLError(MediaError):
    """No registered provider can handle the URL."""


class ProviderUnavailableError(MediaError):
    """A provider's optional dependency is not installed."""


class DownloadError(MediaError):
    """Fetching or post-processing media failed."""
