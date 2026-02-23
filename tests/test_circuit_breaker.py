"""Tests for Transport circuit breaker and exponential backoff."""
import threading
import time
from unittest.mock import MagicMock, call, patch

import pytest

from djust_monitor.transport import (
    CircuitBreaker,
    Transport,
    _CIRCUIT_CLOSED,
    _CIRCUIT_OPEN,
    _CIRCUIT_HALF_OPEN,
)


DSN = "https://testkey@monitor.example.com/api/reports/"


def _make_transport(failure_threshold=5, cooldown=60.0) -> Transport:
    return Transport(dsn=DSN, failure_threshold=failure_threshold, circuit_cooldown=cooldown)


def _mock_response(status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    return resp


# ---------------------------------------------------------------------------
# CircuitBreaker unit tests
# ---------------------------------------------------------------------------


class TestCircuitBreakerStates:
    def test_starts_closed(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown=60.0)
        assert cb.state == _CIRCUIT_CLOSED

    def test_allows_request_when_closed(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown=60.0)
        assert cb.allow_request() is True

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown=60.0)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == _CIRCUIT_OPEN

    def test_does_not_open_before_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown=60.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == _CIRCUIT_CLOSED

    def test_rejects_request_when_open(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown=60.0)
        cb.record_failure()
        assert cb.allow_request() is False

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown=60.0)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        # Two more failures should not open the circuit (count was reset)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == _CIRCUIT_CLOSED

    def test_success_closes_open_circuit(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown=60.0)
        cb.record_failure()
        assert cb.state == _CIRCUIT_OPEN
        cb.record_success()
        assert cb.state == _CIRCUIT_CLOSED


class TestCircuitBreakerHalfOpen:
    def test_becomes_half_open_after_cooldown(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown=0.0)
        cb.record_failure()
        # With cooldown=0 it should immediately transition to HALF_OPEN
        assert cb.state == _CIRCUIT_HALF_OPEN

    def test_allows_one_probe_when_half_open(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown=0.0)
        cb.record_failure()
        # First allow_request() returns True (probe)
        assert cb.allow_request() is True
        # Second concurrent call should be rejected (circuit back to OPEN
        # while the probe is in-flight)
        assert cb.allow_request() is False

    def test_probe_success_closes_circuit(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown=0.0)
        cb.record_failure()
        cb.allow_request()  # take the probe slot
        cb.record_success()
        assert cb.state == _CIRCUIT_CLOSED

    def test_probe_failure_reopens_circuit(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown=60.0)
        cb.record_failure()  # open
        # Force cooldown elapsed
        cb._opened_at = time.monotonic() - 61.0
        cb.allow_request()  # transition to half-open and take probe
        cb.record_failure()  # probe fails → back to OPEN
        assert cb.state == _CIRCUIT_OPEN


class TestCircuitBreakerThreadSafety:
    def test_concurrent_failures_open_circuit_exactly_once(self):
        cb = CircuitBreaker(failure_threshold=5, cooldown=60.0)
        results = []

        def fail_loop():
            for _ in range(5):
                cb.record_failure()
            results.append(cb.state)

        threads = [threading.Thread(target=fail_loop) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(s == _CIRCUIT_OPEN for s in results)


# ---------------------------------------------------------------------------
# Transport circuit breaker integration
# ---------------------------------------------------------------------------


class TestTransportCircuitBreaker:
    def test_circuit_starts_closed(self):
        t = _make_transport()
        assert t.circuit_state == _CIRCUIT_CLOSED

    def test_successful_send_keeps_circuit_closed(self):
        t = _make_transport()
        with patch(
            "djust_monitor.transport.requests.post",
            return_value=_mock_response(200),
        ):
            t._do_send(t.endpoint, {"event": "ok"})
        assert t.circuit_state == _CIRCUIT_CLOSED

    def test_circuit_opens_after_consecutive_failures(self):
        t = _make_transport(failure_threshold=3)
        with patch(
            "djust_monitor.transport.requests.post",
            return_value=_mock_response(500),
        ), patch("djust_monitor.transport.time.sleep"):  # skip backoff delays
            for _ in range(3):
                t._do_send(t.endpoint, {"event": "fail"})
        assert t.circuit_state == _CIRCUIT_OPEN

    def test_open_circuit_drops_send_without_http_call(self):
        t = _make_transport(failure_threshold=1)
        # Open the circuit
        t._circuit.record_failure()
        assert t.circuit_state == _CIRCUIT_OPEN

        with patch("djust_monitor.transport.requests.post") as mock_post:
            t._do_send(t.endpoint, {"event": "should-be-dropped"})
            mock_post.assert_not_called()

    def test_open_circuit_drops_sync_send_returns_false(self):
        t = _make_transport(failure_threshold=1)
        t._circuit.record_failure()

        with patch("djust_monitor.transport.requests.post") as mock_post:
            result = t._do_send_sync(t.endpoint, {"event": "should-be-dropped"})
            mock_post.assert_not_called()
        assert result is False

    def test_circuit_recovers_after_cooldown(self):
        t = _make_transport(failure_threshold=1, cooldown=0.0)
        # Open the circuit
        t._circuit.record_failure()

        with patch(
            "djust_monitor.transport.requests.post",
            return_value=_mock_response(200),
        ):
            result = t._do_send_sync(t.endpoint, {"event": "probe"})

        assert result is True
        assert t.circuit_state == _CIRCUIT_CLOSED

    def test_success_after_partial_failures_resets_circuit(self):
        """Failures below threshold followed by success keep circuit closed."""
        t = _make_transport(failure_threshold=5)
        # Inject two failures directly into the circuit breaker (below threshold)
        t._circuit.record_failure()
        t._circuit.record_failure()
        assert t.circuit_state == _CIRCUIT_CLOSED

        # A successful send resets the count and keeps circuit closed
        with patch(
            "djust_monitor.transport.requests.post",
            return_value=_mock_response(200),
        ):
            t._do_send(t.endpoint, {"event": "ok"})
        assert t.circuit_state == _CIRCUIT_CLOSED


# ---------------------------------------------------------------------------
# Exponential backoff tests
# ---------------------------------------------------------------------------


class TestExponentialBackoff:
    def test_backoff_on_5xx_do_send(self):
        """_do_send retries with exponential backoff on 5xx responses."""
        t = _make_transport(failure_threshold=100)  # don't open circuit
        sleep_calls = []

        def fake_sleep(seconds):
            sleep_calls.append(seconds)

        with patch(
            "djust_monitor.transport.requests.post",
            return_value=_mock_response(500),
        ), patch("djust_monitor.transport.time.sleep", side_effect=fake_sleep):
            t._do_send(t.endpoint, {"event": "fail"})

        # _MAX_RETRIES=3 → 2 sleeps between attempts
        assert len(sleep_calls) == 2
        # Exponential: first sleep >= second sleep / 2
        assert sleep_calls[1] >= sleep_calls[0]

    def test_backoff_on_network_error_do_send_sync(self):
        """_do_send_sync retries with backoff on connection errors."""
        t = _make_transport(failure_threshold=100)
        sleep_calls = []

        def fake_sleep(seconds):
            sleep_calls.append(seconds)

        with patch(
            "djust_monitor.transport.requests.post",
            side_effect=ConnectionError("timeout"),
        ), patch("djust_monitor.transport.time.sleep", side_effect=fake_sleep):
            result = t._do_send_sync(t.endpoint, {"event": "fail"})

        assert result is False
        assert len(sleep_calls) == 2

    def test_no_sleep_on_success(self):
        """No backoff sleep when the first attempt succeeds."""
        t = _make_transport()
        with patch(
            "djust_monitor.transport.requests.post",
            return_value=_mock_response(200),
        ), patch("djust_monitor.transport.time.sleep") as mock_sleep:
            t._do_send(t.endpoint, {"event": "ok"})
            mock_sleep.assert_not_called()

    def test_4xx_does_not_trigger_retry(self):
        """4xx responses are not retried (only 5xx triggers backoff)."""
        t = _make_transport()
        with patch(
            "djust_monitor.transport.requests.post",
            return_value=_mock_response(400),
        ) as mock_post, patch("djust_monitor.transport.time.sleep") as mock_sleep:
            t._do_send(t.endpoint, {"event": "bad-request"})
        # Only one HTTP call — no retry
        assert mock_post.call_count == 1
        mock_sleep.assert_not_called()

    def test_backoff_capped_at_max(self):
        """Sleep duration is capped at _BACKOFF_MAX."""
        from djust_monitor.transport import _BACKOFF_MAX
        t = _make_transport(failure_threshold=100)
        sleep_calls = []

        def fake_sleep(seconds):
            sleep_calls.append(seconds)

        with patch(
            "djust_monitor.transport.requests.post",
            return_value=_mock_response(500),
        ), patch("djust_monitor.transport.time.sleep", side_effect=fake_sleep):
            t._do_send(t.endpoint, {"event": "fail"})

        assert all(s <= _BACKOFF_MAX for s in sleep_calls)
