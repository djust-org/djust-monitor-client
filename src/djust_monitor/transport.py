import json
import logging
import threading
import time
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


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

_CIRCUIT_CLOSED = "closed"      # Normal operation — requests flow through.
_CIRCUIT_OPEN = "open"          # Too many failures — requests are rejected.
_CIRCUIT_HALF_OPEN = "half_open"  # Cooldown elapsed — one probe request allowed.


class CircuitBreaker:
    """Thread-safe circuit breaker for HTTP transport.

    States
    ------
    CLOSED  — All requests pass through. On ``failure_threshold`` consecutive
              failures the circuit moves to OPEN.
    OPEN    — Requests are immediately rejected (no network call). After
              ``cooldown`` seconds the circuit moves to HALF_OPEN.
    HALF_OPEN — One probe request is allowed. Success → CLOSED; failure →
              OPEN again (with a fresh cooldown).

    Parameters
    ----------
    failure_threshold:
        Consecutive failures before the circuit opens. Default 5.
    cooldown:
        Seconds to wait in OPEN state before trying again. Default 60.
    """

    def __init__(self, failure_threshold: int = 5, cooldown: float = 60.0):
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self._lock = threading.Lock()
        self._state = _CIRCUIT_CLOSED
        self._failure_count = 0
        self._opened_at: float = 0.0
        # True while a half-open probe is in-flight so concurrent calls
        # don't all try to probe simultaneously.
        self._probing: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def state(self) -> str:
        with self._lock:
            return self._effective_state()

    def allow_request(self) -> bool:
        """Return True if a request should be attempted right now."""
        with self._lock:
            effective = self._effective_state()
            if effective == _CIRCUIT_CLOSED:
                return True
            if effective == _CIRCUIT_HALF_OPEN:
                # Allow exactly one probe. Mark _probing so subsequent
                # callers see OPEN (not HALF_OPEN) until the probe resolves.
                self._probing = True
                return True
            # OPEN (or HALF_OPEN already probing) — reject
            return False

    def record_success(self) -> None:
        """Record a successful request; reset failure count and close circuit."""
        with self._lock:
            self._failure_count = 0
            self._probing = False
            self._state = _CIRCUIT_CLOSED

    def record_failure(self) -> None:
        """Record a failed request; open circuit if threshold is reached."""
        with self._lock:
            self._probing = False
            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                self._state = _CIRCUIT_OPEN
                self._opened_at = time.monotonic()
                logger.warning(
                    "djust-monitor: circuit breaker opened after %d consecutive failures",
                    self._failure_count,
                )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _effective_state(self) -> str:
        """Compute effective state (handles OPEN → HALF_OPEN transition)."""
        if self._state == _CIRCUIT_OPEN and not self._probing:
            if time.monotonic() - self._opened_at >= self.cooldown:
                return _CIRCUIT_HALF_OPEN
        return self._state


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

# Exponential backoff parameters for retries within a single send attempt.
_BACKOFF_BASE = 0.5   # seconds — first retry wait
_BACKOFF_MAX = 10.0   # seconds — cap
_MAX_RETRIES = 3      # total attempts (1 initial + 2 retries)


class Transport:
    """Fire-and-forget HTTP transport. Sends payloads in background threads.

    Features
    --------
    * Circuit breaker — stops hammering a down server; auto-recovers.
    * Exponential backoff — jitter-free binary-exponential delays between
      retries on 5xx or network errors.
    * Flush — ``flush_exceptions()`` joins in-flight threads before shutdown.
    """

    def __init__(
        self,
        dsn: str,
        timeout: float = 5.0,
        failure_threshold: int = 5,
        circuit_cooldown: float = 60.0,
    ):
        self.endpoint, self.api_key = parse_dsn(dsn)
        self.timeout = timeout
        # Derive metrics endpoint from reports endpoint
        self.metrics_endpoint = self.endpoint.replace("/api/reports/", "/api/metrics/")
        self.logs_endpoint = self.endpoint.replace("/api/reports/", "/api/logs/")
        # Track in-flight exception threads so flush_exceptions() can join them
        self._inflight_lock = threading.Lock()
        self._inflight_threads: list[threading.Thread] = []
        # Shared circuit breaker across all endpoints on this transport
        self._circuit = CircuitBreaker(
            failure_threshold=failure_threshold,
            cooldown=circuit_cooldown,
        )

    # ------------------------------------------------------------------
    # Public send methods
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Circuit-breaker state (for introspection / testing)
    # ------------------------------------------------------------------

    @property
    def circuit_state(self) -> str:
        return self._circuit.state

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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

    def _do_send_sync(self, url: str, payload: dict) -> bool:
        """POST the payload synchronously with exponential backoff. Returns success."""
        if not self._circuit.allow_request():
            logger.debug("djust-monitor: circuit open, dropping sync send to %s", url)
            return False

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = _safe_serialize(payload)
        wait = _BACKOFF_BASE
        for attempt in range(_MAX_RETRIES):
            try:
                resp = requests.post(
                    url,
                    data=body,
                    headers=headers,
                    timeout=self.timeout,
                )
                if resp.status_code < 500:
                    self._circuit.record_success()
                    return True
                # 5xx — back off and retry
                self._circuit.record_failure()
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(min(wait, _BACKOFF_MAX))
                    wait *= 2
            except Exception:
                logger.debug("djust-monitor: transport error", exc_info=True)
                self._circuit.record_failure()
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(min(wait, _BACKOFF_MAX))
                    wait *= 2
        return False

    def _do_send(self, url: str, payload: dict) -> None:
        """POST the payload with exponential backoff. Respects circuit breaker."""
        if not self._circuit.allow_request():
            logger.debug("djust-monitor: circuit open, dropping send to %s", url)
            return

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = _safe_serialize(payload)
        wait = _BACKOFF_BASE
        for attempt in range(_MAX_RETRIES):
            try:
                resp = requests.post(
                    url,
                    data=body,
                    headers=headers,
                    timeout=self.timeout,
                )
                if resp.status_code < 500:
                    self._circuit.record_success()
                    return
                # 5xx — back off and retry
                self._circuit.record_failure()
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(min(wait, _BACKOFF_MAX))
                    wait *= 2
            except Exception:
                logger.debug("djust-monitor: transport error", exc_info=True)
                self._circuit.record_failure()
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(min(wait, _BACKOFF_MAX))
                    wait *= 2
