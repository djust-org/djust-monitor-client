from djust_monitor.client import DjustErrorsClient

VERSION = "0.1.0"

_client: DjustErrorsClient | None = None


def init(dsn: str, **kwargs) -> DjustErrorsClient:
    """Initialize the global error client with a DSN."""
    global _client
    _client = DjustErrorsClient(dsn=dsn, **kwargs)
    return _client


def capture_exception(exc: BaseException, context: dict | None = None) -> str | None:
    """Capture an exception and send it to the server. Returns the fingerprint."""
    if _client is None:
        return None
    return _client.capture(exc, context=context)


def record_metric(metric: dict) -> None:
    """Record a request metric (buffered, flushed in batches)."""
    if _client is None:
        return
    _client.record_metric(metric)


def flush_metrics() -> None:
    """Flush any buffered metrics immediately."""
    if _client is None:
        return
    _client.flush_metrics()
