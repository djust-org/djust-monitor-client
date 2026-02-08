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


class Transport:
    """Fire-and-forget HTTP transport. Sends payloads in background threads."""

    def __init__(self, dsn: str, timeout: float = 5.0):
        self.endpoint, self.api_key = parse_dsn(dsn)
        self.timeout = timeout
        # Derive metrics endpoint from reports endpoint
        self.metrics_endpoint = self.endpoint.replace("/api/reports/", "/api/metrics/")

    def send(self, payload: dict) -> None:
        """Send payload in a background thread (non-blocking)."""
        t = threading.Thread(target=self._do_send, args=(self.endpoint, payload), daemon=True)
        t.start()

    def send_metrics(self, batch: list[dict]) -> None:
        """Send a batch of metrics in a background thread (non-blocking)."""
        payload = {"metrics": batch}
        t = threading.Thread(
            target=self._do_send, args=(self.metrics_endpoint, payload), daemon=True
        )
        t.start()

    def _do_send(self, url: str, payload: dict) -> None:
        """POST the payload with one retry on 5xx."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        for attempt in range(2):
            try:
                resp = requests.post(
                    url,
                    json=payload,
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
