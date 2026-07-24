"""YouTube worker adapter package.

Service exports are resolved lazily so importing a low-level adapter such as
``worker.youtube.errors`` cannot recursively import the legacy caption module.
"""

from importlib import import_module
from typing import Any

from .errors import YouTubeError, YouTubeErrorKind, classify_youtube_error
from .yt_dlp_executor import YtDlpError, YtDlpExecutionResult, YtDlpExecutor

_SERVICE_EXPORTS = {
    "YouTubeCaptionFetchError",
    "YouTubeCaptionRateLimitError",
    "YouTubeCaptionResult",
    "YouTubeService",
    "download_audio",
    "fetch_auto_captions",
    "fetch_metadata",
    "get_youtube_service",
}


def __getattr__(name: str) -> Any:
    if name not in _SERVICE_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    service = import_module("worker.youtube.service")
    value = getattr(service, name)
    globals()[name] = value
    return value


__all__ = [
    "YouTubeError",
    "YouTubeErrorKind",
    "classify_youtube_error",
    "YtDlpError",
    "YtDlpExecutionResult",
    "YtDlpExecutor",
    *_SERVICE_EXPORTS,
]
