from djust_monitor.client import DjustErrorsClient
from djust_monitor.logging_handler import BatchingHTTPHandler  # noqa: F401

VERSION = "0.1.0"

# Re-export for convenient imports:
#   INSTALLED_APPS = ["djust_monitor"]           (auto-discovers apps.py on Django 3.2+)
#   MIDDLEWARE = ["djust_monitor.django.DjustMonitorMiddleware"]
from djust_monitor.django import DjustMonitorMiddleware  # noqa: F401

# Django <3.2 fallback (harmless no-op on 3.2+)
default_app_config = "djust_monitor.apps.DjustMonitorConfig"

_client: DjustErrorsClient | None = None


def init(dsn: str, **kwargs) -> DjustErrorsClient:
    """Initialize the global error client with a DSN."""
    global _client
    _client = DjustErrorsClient(dsn=dsn, **kwargs)
    return _client


def capture_exception(
    exc: BaseException,
    context: dict | None = None,
    send_sync: bool = False,
) -> str | None:
    """Capture an exception and send it to the server.

    Args:
        exc: The exception to capture.
        context: Optional request/user context dict.
        send_sync: When True, block until HTTP delivery completes before
            returning. Use for critical events (e.g. pipeline failures)
            where you need confirmation before proceeding. Default False.

    Returns:
        The fingerprint string, or None if client is uninitialised or sampled out.
    """
    if _client is None:
        return None
    return _client.capture(exc, context=context, send_sync=send_sync)


def flush_exceptions(timeout: float = 5.0) -> None:
    """Wait for all in-flight exception transport threads to complete.

    Blocks until every background exception send thread has finished
    (or the per-thread timeout elapses). Use at shutdown or before a
    process restart to avoid losing in-flight reports.

    Args:
        timeout: Maximum seconds to wait per thread. Default 5.0.
    """
    if _client is None:
        return
    _client.flush_exceptions(timeout=timeout)


def record_metric(metric: dict) -> None:
    """Record a request metric (buffered, flushed in batches)."""
    if _client is None:
        return
    _client.record_metric(metric)


def capture_event(
    event_type: str,
    message: str,
    context: dict | None = None,
    source: str = "python",
) -> str | None:
    """Capture a non-exception event (e.g. FullHTMLUpdate) and send to the server."""
    if _client is None:
        return None
    return _client.capture_event(event_type, message, context=context, source=source)


def flush_metrics() -> None:
    """Flush any buffered metrics immediately."""
    if _client is None:
        return
    _client.flush_metrics()
