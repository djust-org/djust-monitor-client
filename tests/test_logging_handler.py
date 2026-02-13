"""Tests for BatchingHTTPHandler."""

import logging
import time
from unittest.mock import MagicMock, patch

from djust_monitor.logging_handler import BatchingHTTPHandler


def _make_handler(dsn="https://testkey123@monitor.example.com/api/reports/", **kwargs):
    """Create a handler with a mocked transport."""
    with patch("djust_monitor.logging_handler.atexit"):
        handler = BatchingHTTPHandler(dsn=dsn, flush_interval=999, **kwargs)
    handler.transport = MagicMock()
    handler.transport.send_logs = MagicMock(return_value=True)
    return handler


class TestEmit:
    def test_log_message_queued(self):
        handler = _make_handler()
        logger = logging.getLogger("test.emit")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        logger.info("hello world")

        assert len(handler._queue) == 1
        entry = handler._queue[0]
        assert entry["level"] == logging.INFO
        assert entry["level_name"] == "INFO"
        assert entry["logger_name"] == "test.emit"
        assert "hello world" in entry["message"]

        logger.removeHandler(handler)

    def test_record_fields(self):
        handler = _make_handler(environment="prod", service="myapp", version="1.0")
        record = logging.LogRecord(
            name="mylogger", level=logging.WARNING, pathname="test.py",
            lineno=42, msg="test message", args=(), exc_info=None,
        )
        handler.emit(record)

        entry = handler._queue[0]
        assert entry["environment"] == "prod"
        assert entry["service"] == "myapp"
        assert entry["version"] == "1.0"
        assert entry["level"] == logging.WARNING
        assert isinstance(entry["timestamp"], float)

    def test_tenant_id_from_record(self):
        handler = _make_handler()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="msg", args=(), exc_info=None,
        )
        record.tenant_id = "acme"
        handler.emit(record)

        assert handler._queue[0]["tenant_id"] == "acme"

    def test_request_id_from_record(self):
        handler = _make_handler()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="msg", args=(), exc_info=None,
        )
        record.request_id = "req-123"
        handler.emit(record)

        assert handler._queue[0]["request_id"] == "req-123"

    def test_extra_fields_captured(self):
        handler = _make_handler()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="msg", args=(), exc_info=None,
        )
        record.user_email = "user@example.com"
        handler.emit(record)

        assert handler._queue[0]["extra"]["user_email"] == "user@example.com"


class TestBatching:
    def test_flush_triggers_at_batch_size(self):
        handler = _make_handler(batch_size=3)
        for i in range(3):
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=0,
                msg=f"msg {i}", args=(), exc_info=None,
            )
            handler.emit(record)

        handler.transport.send_logs.assert_called_once()
        batch = handler.transport.send_logs.call_args[0][0]
        assert len(batch) == 3

    def test_no_flush_below_batch_size(self):
        handler = _make_handler(batch_size=10)
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="msg", args=(), exc_info=None,
        )
        handler.emit(record)

        handler.transport.send_logs.assert_not_called()
        assert len(handler._queue) == 1

    def test_manual_flush(self):
        handler = _make_handler()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="msg", args=(), exc_info=None,
        )
        handler.emit(record)
        handler._flush()

        handler.transport.send_logs.assert_called_once()
        assert len(handler._queue) == 0


class TestQueueOverflow:
    def test_drops_oldest_on_overflow(self):
        handler = _make_handler(max_queue_size=3)
        for i in range(5):
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=0,
                msg=f"msg {i}", args=(), exc_info=None,
            )
            handler.emit(record)

        assert len(handler._queue) == 3
        messages = [e["message"] for e in handler._queue]
        assert "msg 0" not in messages
        assert "msg 1" not in messages
        assert "msg 4" in messages


class TestCircuitBreaker:
    def test_opens_after_consecutive_failures(self):
        handler = _make_handler(batch_size=1)
        handler.transport.send_logs.return_value = False

        for i in range(6):
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=0,
                msg=f"msg {i}", args=(), exc_info=None,
            )
            handler.emit(record)

        assert handler._consecutive_failures >= 5
        assert handler._backoff_until > 0

    def test_resets_on_success(self):
        handler = _make_handler(batch_size=1)
        handler.transport.send_logs.return_value = False

        # Cause some failures
        for i in range(3):
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=0,
                msg=f"fail {i}", args=(), exc_info=None,
            )
            handler.emit(record)

        assert handler._consecutive_failures == 3

        # Now succeed
        handler.transport.send_logs.return_value = True
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="success", args=(), exc_info=None,
        )
        handler.emit(record)

        assert handler._consecutive_failures == 0


class TestClose:
    def test_close_flushes_remaining(self):
        handler = _make_handler()
        for i in range(3):
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=0,
                msg=f"msg {i}", args=(), exc_info=None,
            )
            handler.emit(record)

        handler.close()

        handler.transport.send_logs.assert_called_once()
        assert len(handler._queue) == 0

    def test_close_idempotent(self):
        handler = _make_handler()
        handler.close()
        handler.close()  # Should not raise


class TestTenantAutoInjection:
    def test_fallback_to_threadlocal(self):
        handler = _make_handler()

        mock_tenant = MagicMock()
        mock_tenant.id = "tenant-42"

        with patch(
            "djust_monitor.logging_handler.BatchingHTTPHandler._get_tenant_from_threadlocal",
            return_value="tenant-42",
        ):
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=0,
                msg="msg", args=(), exc_info=None,
            )
            handler.emit(record)

        assert handler._queue[0]["tenant_id"] == "tenant-42"

    def test_empty_when_no_tenant(self):
        handler = _make_handler()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="msg", args=(), exc_info=None,
        )
        handler.emit(record)

        assert handler._queue[0]["tenant_id"] == ""


class TestDictConfig:
    def test_handler_works_via_dictconfig(self):
        """Verify handler can be configured via logging.config.dictConfig."""
        import logging.config

        with patch("djust_monitor.logging_handler.atexit"):
            config = {
                "version": 1,
                "disable_existing_loggers": False,
                "handlers": {
                    "test_djust": {
                        "class": "djust_monitor.BatchingHTTPHandler",
                        "dsn": "https://key123@monitor.example.com/api/reports/",
                        "environment": "test",
                        "service": "testapp",
                    },
                },
                "loggers": {
                    "test.dictconfig": {
                        "handlers": ["test_djust"],
                        "level": "INFO",
                    },
                },
            }
            logging.config.dictConfig(config)

        logger = logging.getLogger("test.dictconfig")
        handler = logger.handlers[0]
        assert isinstance(handler, BatchingHTTPHandler)
        assert handler.environment == "test"
        assert handler.service == "testapp"

        logger.removeHandler(handler)
        handler.close()
