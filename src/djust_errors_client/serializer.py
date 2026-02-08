import os
import platform
import sys
import traceback
from datetime import datetime, timezone


def serialize_exception(
    exc: BaseException,
    *,
    environment: str = "production",
    release: str = "",
    request_context: dict | None = None,
) -> dict:
    """Convert an exception into a structured JSON-serializable payload."""
    tb_exc = traceback.TracebackException.from_exception(exc, capture_locals=True)
    frames = _extract_frames(tb_exc)

    payload = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "environment": environment,
        "release": release,
        "exception": {
            "type": type(exc).__name__,
            "message": str(exc),
            "frames": frames,
        },
        "context": {
            "python_version": platform.python_version(),
            "django_version": _get_django_version(),
            "os": platform.system(),
            "hostname": platform.node(),
        },
    }

    if request_context:
        payload["request"] = request_context

    return payload


def _extract_frames(tb_exc: traceback.TracebackException) -> list[dict]:
    """Extract stack frames from a TracebackException."""
    frames = []
    for frame_summary in tb_exc.stack:
        frame_data: dict = {
            "file": frame_summary.filename,
            "line": frame_summary.lineno,
            "function": frame_summary.name,
            "context_line": frame_summary.line or "",
        }
        if frame_summary.locals:
            frame_data["locals"] = {
                k: _safe_repr(v) for k, v in frame_summary.locals.items()
            }
        frames.append(frame_data)
    return frames


def _safe_repr(value: str) -> str:
    """Safely convert a local variable repr string, truncating if needed."""
    if len(value) > 1024:
        return value[:1024] + "..."
    return value


def _get_django_version() -> str:
    try:
        import django

        return django.get_version()
    except ImportError:
        return ""
