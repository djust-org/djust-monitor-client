import random
import threading

from djust_errors_client.fingerprint import compute_fingerprint
from djust_errors_client.scrubber import scrub
from djust_errors_client.serializer import serialize_exception
from djust_errors_client.transport import Transport


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

    def capture(self, exc: BaseException, context: dict | None = None) -> str | None:
        """Serialize, fingerprint, scrub, and send an exception report."""
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
