import random
import threading

from djust_monitor.fingerprint import compute_fingerprint
from djust_monitor.scrubber import scrub
from djust_monitor.serializer import serialize_exception
from djust_monitor.transport import Transport


class DjustErrorsClient:
    """Singleton-style error client that serializes, fingerprints, scrubs, and sends."""

    def __init__(
        self,
        dsn: str,
        environment: str = "production",
        release: str = "",
        sample_rate: float = 1.0,
        metrics_batch_size: int = 50,
        metrics_flush_interval: float = 60.0,
    ):
        if not (0.0 <= sample_rate <= 1.0):
            raise ValueError(f"sample_rate must be between 0.0 and 1.0, got {sample_rate!r}")
        self.transport = Transport(dsn)
        self.environment = environment
        self.release = release
        self.sample_rate = sample_rate
        self._lock = threading.Lock()

        # Metrics buffer
        self._metrics_batch_size = metrics_batch_size
        self._metrics_flush_interval = metrics_flush_interval
        self._metrics_buffer: list[dict] = []
        self._metrics_lock = threading.Lock()
        self._flush_timer: threading.Timer | None = None
        self._start_flush_timer()

    def _start_flush_timer(self):
        """Start the periodic flush timer for metrics."""
        if self._metrics_flush_interval <= 0:
            return
        self._flush_timer = threading.Timer(
            self._metrics_flush_interval, self._periodic_flush
        )
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _periodic_flush(self):
        """Flush metrics buffer on timer, then restart timer."""
        self.flush_metrics()
        self._start_flush_timer()

    def capture(
        self,
        exc: BaseException,
        context: dict | None = None,
        send_sync: bool = False,
    ) -> str | None:
        """Serialize, fingerprint, scrub, and send an exception report.

        Args:
            exc: The exception to capture.
            context: Optional request/user context dict.
            send_sync: When True, block until the HTTP send completes before
                returning. Use for critical events (e.g. pipeline failures)
                where confirmation of delivery is required. Default False.

        Returns:
            The fingerprint string, or None if sampled out.
        """
        if self.sample_rate < 1.0 and random.random() > self.sample_rate:
            return None

        payload = serialize_exception(
            exc,
            environment=self.environment,
            release=self.release,
            request_context=context,
        )
        fingerprint = compute_fingerprint(payload)
        payload["fingerprint"] = fingerprint
        scrub(payload)
        if send_sync:
            self.transport.send_sync(payload)
        else:
            self.transport.send(payload)
        return fingerprint

    def flush_exceptions(self, timeout: float = 5.0) -> None:
        """Block until all in-flight exception sends complete (or timeout).

        Args:
            timeout: Maximum seconds to wait per thread. Default 5.0.
        """
        self.transport.flush_exceptions(timeout=timeout)

    def capture_event(
        self,
        event_type: str,
        message: str,
        context: dict | None = None,
        source: str = "python",
    ) -> str | None:
        """Send a non-exception event report (e.g. FullHTMLUpdate, performance warning)."""
        if self.sample_rate < 1.0 and random.random() > self.sample_rate:
            return None

        payload = {
            "source": source,
            "environment": self.environment,
            "release": self.release,
            "exception": {
                "type": event_type,
                "message": message,
                "frames": [],
            },
            "context": context or {},
        }
        fingerprint = compute_fingerprint(payload)
        payload["fingerprint"] = fingerprint
        self.transport.send(payload)
        return fingerprint

    def record_metric(self, metric: dict) -> None:
        """Buffer a request metric. Flushes when batch size is reached."""
        metric.setdefault("environment", self.environment)
        batch = None
        with self._metrics_lock:
            self._metrics_buffer.append(metric)
            if len(self._metrics_buffer) >= self._metrics_batch_size:
                batch = self._metrics_buffer
                self._metrics_buffer = []
        if batch:
            self.transport.send_metrics(batch)

    def flush_metrics(self) -> None:
        """Flush any buffered metrics immediately."""
        with self._metrics_lock:
            if not self._metrics_buffer:
                return
            batch = self._metrics_buffer
            self._metrics_buffer = []
        self.transport.send_metrics(batch)
