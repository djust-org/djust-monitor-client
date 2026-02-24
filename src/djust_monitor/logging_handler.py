"""Batching HTTP handler that ships logs to monitor.djust.org."""

import atexit
import collections
import logging
import sys
import threading
import time


class BatchingHTTPHandler(logging.Handler):
    """Ships logs to monitor.djust.org with queue-based batching.

    Usage in Django LOGGING settings::

        LOGGING = {
            "version": 1,
            "handlers": {
                "djust_monitor": {
                    "class": "djust_monitor.BatchingHTTPHandler",
                    "dsn": os.environ["DJUST_MONITOR_DSN"],
                    "environment": "production",
                    "service": "myapp",
                },
            },
            "loggers": {
                "djust_tenants.audit": {
                    "handlers": ["djust_monitor"],
                    "level": "INFO",
                },
            },
        }
    """

    def __init__(
        self,
        dsn,
        environment="",
        service="",
        version="",
        batch_size=100,
        flush_interval=5.0,
        max_queue_size=1000,
    ):
        super().__init__()
        from djust_monitor.transport import Transport

        self.transport = Transport(dsn)
        self.environment = environment
        self.service = service
        self.version = version
        self.batch_size = batch_size
        self.flush_interval = flush_interval

        self._queue = collections.deque(maxlen=max_queue_size)
        self._lock = threading.Lock()
        self._timer = None
        self._closed = False

        # Circuit breaker
        self._consecutive_failures = 0
        self._backoff_until = 0.0
        self._max_failures = 5
        self._max_backoff = 60.0

        self._start_timer()
        atexit.register(self.close)

    def _start_timer(self):
        if self._closed:
            return
        self._timer = threading.Timer(self.flush_interval, self._timer_flush)
        self._timer.daemon = True
        self._timer.start()

    def _timer_flush(self):
        self._flush()
        self._start_timer()

    def emit(self, record):
        try:
            entry = self._record_to_dict(record)
            with self._lock:
                self._queue.append(entry)
                should_flush = len(self._queue) >= self.batch_size
            if should_flush:
                self._flush()
        except Exception:
            self.handleError(record)

    def _record_to_dict(self, record):
        tenant_id = getattr(record, "tenant_id", "")
        if not tenant_id:
            tenant_id = self._get_tenant_from_threadlocal()

        # Collect extra fields (anything the user attached beyond standard attrs)
        standard_attrs = {
            "name", "msg", "args", "created", "relativeCreated", "exc_info",
            "exc_text", "stack_info", "lineno", "funcName", "pathname",
            "filename", "module", "thread", "threadName", "process",
            "processName", "levelname", "levelno", "message", "msecs",
            "taskName",
            # Our custom attributes
            "tenant_id", "request_id",
        }
        extra = {
            k: v for k, v in record.__dict__.items()
            if k not in standard_attrs and not k.startswith("_")
        }

        return {
            "timestamp": record.created,
            "level": record.levelno,
            "level_name": record.levelname,
            "logger_name": record.name,
            "message": self.format(record),
            "tenant_id": tenant_id,
            "request_id": getattr(record, "request_id", ""),
            "environment": self.environment,
            "service": self.service,
            "version": self.version,
            "extra": extra,
        }

    def _get_tenant_from_threadlocal(self):
        try:
            from djust_tenants.middleware import get_current_tenant
            tenant = get_current_tenant()
            if tenant:
                return str(getattr(tenant, "id", tenant))
        except (ImportError, Exception):
            pass
        return ""

    def _flush(self):
        with self._lock:
            if not self._queue:
                return
            # Drain up to 500 entries
            batch = []
            for _ in range(min(500, len(self._queue))):
                batch.append(self._queue.popleft())

        # Check circuit breaker
        now = time.monotonic()
        if self._consecutive_failures >= self._max_failures:
            if now < self._backoff_until:
                print(  # noqa: Q001 — can't use logging here (infinite recursion risk)
                    f"djust-monitor: circuit open, dropping {len(batch)} logs "
                    f"(backoff {self._backoff_until - now:.0f}s remaining)",
                    file=sys.stderr,
                )
                return
            # Backoff expired — try again

        success = self.transport.send_logs(batch)
        if success:
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1
            backoff = min(2 ** self._consecutive_failures, self._max_backoff)
            self._backoff_until = time.monotonic() + backoff

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._timer is not None:
            self._timer.cancel()
        self._flush()
        super().close()
