"""Tests for the djust_monitor top-level public API (djust_monitor/__init__.py).

Covers:
- uninitialised-client no-ops (all public functions must not raise)
- init() creates and returns a client
- capture_exception / capture_event / record_metric / flush_exceptions delegates
- flush_metrics delegates
"""

import pytest
from unittest.mock import MagicMock, patch

import djust_monitor


@pytest.fixture(autouse=True)
def _reset_client():
    """Ensure the global _client is reset between tests."""
    original = djust_monitor._client
    djust_monitor._client = None
    yield
    djust_monitor._client = None
    # Don't restore to original — tests should be self-contained


# ---------------------------------------------------------------------------
# Uninitialised client — all public functions must be silent no-ops
# ---------------------------------------------------------------------------


class TestUninitialisedClient:
    def test_capture_exception_returns_none(self):
        assert djust_monitor.capture_exception(ValueError("oops")) is None

    def test_capture_event_returns_none(self):
        assert djust_monitor.capture_event("TestEvent", "msg") is None

    def test_flush_exceptions_does_not_raise(self):
        djust_monitor.flush_exceptions()  # should silently return

    def test_flush_metrics_does_not_raise(self):
        djust_monitor.flush_metrics()  # should silently return

    def test_record_metric_does_not_raise(self):
        djust_monitor.record_metric({"path": "/", "status_code": 200})


# ---------------------------------------------------------------------------
# init()
# ---------------------------------------------------------------------------


class TestInit:
    def test_returns_client_instance(self):
        with patch("djust_monitor.DjustErrorsClient") as MockClient:
            MockClient.return_value = MagicMock()
            client = djust_monitor.init("http://key@host/api/reports/")
            assert client is MockClient.return_value

    def test_sets_global_client(self):
        with patch("djust_monitor.DjustErrorsClient") as MockClient:
            MockClient.return_value = MagicMock()
            djust_monitor.init("http://key@host/api/reports/")
            assert djust_monitor._client is MockClient.return_value

    def test_kwargs_forwarded_to_client(self):
        with patch("djust_monitor.DjustErrorsClient") as MockClient:
            MockClient.return_value = MagicMock()
            djust_monitor.init(
                "http://key@host/api/reports/",
                environment="staging",
                release="v1.2.3",
                sample_rate=0.5,
            )
            MockClient.assert_called_once_with(
                dsn="http://key@host/api/reports/",
                environment="staging",
                release="v1.2.3",
                sample_rate=0.5,
            )

    def test_reinit_replaces_old_client(self):
        with patch("djust_monitor.DjustErrorsClient") as MockClient:
            first = MagicMock(name="first")
            second = MagicMock(name="second")
            MockClient.side_effect = [first, second]
            djust_monitor.init("http://key@host/api/reports/")
            djust_monitor.init("http://key2@host/api/reports/")
            assert djust_monitor._client is second


# ---------------------------------------------------------------------------
# capture_exception
# ---------------------------------------------------------------------------


class TestCaptureException:
    def _init(self, **kwargs):
        mock_client = MagicMock()
        djust_monitor._client = mock_client
        return mock_client

    def test_delegates_to_client_capture(self):
        mock_client = self._init()
        exc = ValueError("test error")
        djust_monitor.capture_exception(exc)
        mock_client.capture.assert_called_once_with(exc, context=None, send_sync=False)

    def test_passes_context(self):
        mock_client = self._init()
        exc = ValueError("test")
        ctx = {"user_id": 42}
        djust_monitor.capture_exception(exc, context=ctx)
        mock_client.capture.assert_called_once_with(exc, context=ctx, send_sync=False)

    def test_passes_send_sync(self):
        mock_client = self._init()
        exc = ValueError("test")
        djust_monitor.capture_exception(exc, send_sync=True)
        mock_client.capture.assert_called_once_with(exc, context=None, send_sync=True)

    def test_returns_fingerprint_from_client(self):
        mock_client = self._init()
        mock_client.capture.return_value = "abc123"
        result = djust_monitor.capture_exception(ValueError("x"))
        assert result == "abc123"

    def test_returns_none_when_sampled_out(self):
        mock_client = self._init()
        mock_client.capture.return_value = None
        result = djust_monitor.capture_exception(ValueError("x"))
        assert result is None


# ---------------------------------------------------------------------------
# capture_event
# ---------------------------------------------------------------------------


class TestCaptureEvent:
    def _init(self):
        mock_client = MagicMock()
        djust_monitor._client = mock_client
        return mock_client

    def test_delegates_to_client(self):
        mock_client = self._init()
        djust_monitor.capture_event("DJE-053", "full html update")
        mock_client.capture_event.assert_called_once_with(
            "DJE-053", "full html update", context=None, source="python"
        )

    def test_passes_context(self):
        mock_client = self._init()
        ctx = {"view": "MyView"}
        djust_monitor.capture_event("Evt", "msg", context=ctx)
        mock_client.capture_event.assert_called_once_with(
            "Evt", "msg", context=ctx, source="python"
        )

    def test_passes_source(self):
        mock_client = self._init()
        djust_monitor.capture_event("Evt", "msg", source="javascript")
        mock_client.capture_event.assert_called_once_with(
            "Evt", "msg", context=None, source="javascript"
        )

    def test_returns_fingerprint(self):
        mock_client = self._init()
        mock_client.capture_event.return_value = "fp-xyz"
        result = djust_monitor.capture_event("E", "m")
        assert result == "fp-xyz"


# ---------------------------------------------------------------------------
# flush_exceptions / flush_metrics / record_metric
# ---------------------------------------------------------------------------


class TestFlushAndRecord:
    def _init(self):
        mock_client = MagicMock()
        djust_monitor._client = mock_client
        return mock_client

    def test_flush_exceptions_delegates(self):
        mock_client = self._init()
        djust_monitor.flush_exceptions(timeout=3.0)
        mock_client.flush_exceptions.assert_called_once_with(timeout=3.0)

    def test_flush_exceptions_default_timeout(self):
        mock_client = self._init()
        djust_monitor.flush_exceptions()
        mock_client.flush_exceptions.assert_called_once_with(timeout=5.0)

    def test_flush_metrics_delegates(self):
        mock_client = self._init()
        djust_monitor.flush_metrics()
        mock_client.flush_metrics.assert_called_once()

    def test_record_metric_delegates(self):
        mock_client = self._init()
        metric = {"path": "/health", "status_code": 200, "duration_ms": 12.5}
        djust_monitor.record_metric(metric)
        mock_client.record_metric.assert_called_once_with(metric)
