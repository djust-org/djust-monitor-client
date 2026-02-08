import hashlib
import re


def compute_fingerprint(payload: dict) -> str:
    """Compute a SHA-256 fingerprint for deduplication.

    Uses: exception class, normalized message, and top application frame.
    """
    exc_data = payload.get("exception", {})
    exc_type = exc_data.get("type", "")
    message = _normalize_message(exc_data.get("message", ""))
    top_frame = _get_top_app_frame(exc_data.get("frames", []))

    parts = [exc_type, message, top_frame]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_message(message: str) -> str:
    """Remove variable parts from exception messages for stable fingerprinting."""
    # Replace hex addresses like 0x7f1a2b3c4d5e
    message = re.sub(r"0x[0-9a-fA-F]+", "<addr>", message)
    # Replace UUIDs (before numeric IDs to avoid partial matches)
    message = re.sub(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        "<uuid>",
        message,
    )
    # Replace ISO timestamps (before numeric IDs to avoid partial matches)
    message = re.sub(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}",
        "<timestamp>",
        message,
    )
    # Replace numeric IDs
    message = re.sub(r"\b\d{4,}\b", "<id>", message)
    return message


def _get_top_app_frame(frames: list[dict]) -> str:
    """Get the top application frame (not stdlib/site-packages) as a string."""
    for frame in reversed(frames):
        filepath = frame.get("file", "")
        if "site-packages" in filepath or "lib/python" in filepath:
            continue
        return f"{filepath}:{frame.get('function', '')}"
    # Fall back to the very last frame if all are library frames
    if frames:
        f = frames[-1]
        return f"{f.get('file', '')}:{f.get('function', '')}"
    return ""
