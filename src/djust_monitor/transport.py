import json
import logging
import threading
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)


def parse_dsn(dsn: str) -> tuple[str, str]:
    """Parse DSN into (endpoint_url, api_key).

    DSN format: https://<api_key>@<host>/<path>
    Returns: ("https://<host>/<path>", "<api_key>")
    """
    parsed = urlparse(dsn)
    if not parsed.username:
        raise ValueError(f"Invalid DSN: missing API key (username component): {dsn}")
    api_key = parsed.username
    # Reconstruct URL without credentials
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    endpoint = f"{parsed.scheme}://{host}{port}{parsed.path}"
    return endpoint, api_key


def _safe_serialize(payload: dict) -> str:
    """Serialize payload to a JSON string, falling back gracefully on errors.

    If the payload contains non-serializable values (Django model instances,
    exceptions with circular __context__ references, etc.), those values are
    replaced with their str() representation so the report is never silently
    dropped.
    """
    def _default(obj):
        return str(obj)

    try:
        return json.dumps(payload, default=_default)
    except (TypeError, ValueError, RecursionError) as exc:
        # Last-resort: represent the whole payload as a string so we at least
        # send something meaningful rather than dropping the event.
        logger.debug("djust-monitor: payload serialization fallback: %s", exc)
        return json.dumps({"_serialization_error": str(exc), "raw": str(payload)})


class Transport:
    """Fire-and-forget HTTP transport. Sends payloads in background threads."""

    def __init__(self, dsn: str, timeout: float = 5.0):
        self.endpoint, self.api_key = parse_dsn(dsn)
        self.timeout = timeout
        # Derive metrics endpoint from reports endpoint
        self.metrics_endpoint = self.endpoint.replace("/api/reports/", "/api/metrics/")
        self.logs_endpoint = self.endpoint.replace("/api/reports/", "/api/logs/")
        # Track in-flight exception threads so flush_exceptions() can join them
        self._inflight_lock = threading.Lock()
        self._inflight_threads: list[threading.Thread] = []

    def send(self, payload: dict) -> None:
        """Send payload in a background thread (non-blocking)."""
        t = threading.Thread(target=self._tracked_send, args=(payload,), daemon=True)
        with self._inflight_lock:
            self._inflight_threads.append(t)
        t.start()

    def send_sync(self, payload: dict) -> bool:
        """Send payload synchronously. Returns True on success.

        Use for critical events (e.g. pipeline failures) where confirmation
        is needed before proceeding.
        """
        return self._do_send_sync(self.endpoint, payload)

    def flush_exceptions(self, timeout: float = 5.0) -> None:
        """Block until all in-flight exception transport threads finish (or timeout)."""
        with self._inflight_lock:
            threads = list(self._inflight_threads)
        for t in threads:
            t.join(timeout=timeout)

    def _tracked_send(self, payload: dict) -> None:
        """Wrapper that removes thread from inflight list after completion."""
        try:
            self._do_send(self.endpoint, payload)
        finally:
            current = threading.current_thread()
            with self._inflight_lock:
                try:
                    self._inflight_threads.remove(current)
                except ValueError:
                    pass

    def send_metrics(self, batch: list[dict]) -> None:
        """Send a batch of metrics in a background thread (non-blocking)."""
        payload = {"metrics": batch}
        t = threading.Thread(
            target=self._do_send, args=(self.metrics_endpoint, payload), daemon=True
        )
        t.start()

    def send_logs(self, batch: list[dict]) -> bool:
        """Send log batch synchronously. Returns True on success.

        Called from the handler's background timer thread, so no need
        for another background thread.
        """
        payload = {"logs": batch}
        return self._do_send_sync(self.logs_endpoint, payload)

    def _do_send_sync(self, url: str, payload: dict) -> bool:
        """POST the payload synchronously with one retry on 5xx. Returns success."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = _safe_serialize(payload)
        for attempt in range(2):
            try:
                resp = requests.post(
                    url,
                    data=body,
                    headers=headers,
                    timeout=self.timeout,
                )
                if resp.status_code < 500:
                    return True
                if attempt == 0:
                    continue
            except Exception:
                logger.debug("djust-monitor: transport error", exc_info=True)
                return False
        return False

    def _do_send(self, url: str, payload: dict) -> None:
        """POST the payload with one retry on 5xx."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = _safe_serialize(payload)
        for attempt in range(2):
            try:
                resp = requests.post(
                    url,
                    data=body,
                    headers=headers,
                    timeout=self.timeout,
                )
                if resp.status_code < 500:
                    return
                # 5xx — retry once
                if attempt == 0:
                    continue
            except Exception:
                logger.debug("djust-monitor: transport error", exc_info=True)
                return
