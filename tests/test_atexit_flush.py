"""Tests for atexit flush registration in DjustErrorsClient."""
import atexit
from unittest.mock import MagicMock, patch

import pytest

from djust_monitor.client import DjustErrorsClient


def _make_client(**kwargs) -> DjustErrorsClient:
    """Return a client with a mocked Transport."""
    with patch("djust_monitor.client.Transport"):
        return DjustErrorsClient(dsn="http://fake@host/api/reports/", **kwargs)


class TestAtexitFlush:
    def test_flush_exceptions_registered_at_init(self):
        """flush_exceptions must be registered with atexit on client init."""
        with patch("djust_monitor.client.atexit.register") as mock_register, \
             patch("djust_monitor.client.Transport"):
            client = DjustErrorsClient(dsn="http://fake@host/api/reports/")

        mock_register.assert_called_once_with(client.flush_exceptions)

    def test_flush_exceptions_registered_exactly_once_per_client(self):
        """Each client instance registers its own atexit handler independently."""
        registered = []

        original_register = atexit.register

        def capturing_register(fn, *args, **kwargs):
            registered.append(fn)
            return original_register(fn, *args, **kwargs)

        with patch("djust_monitor.client.atexit.register", side_effect=capturing_register), \
             patch("djust_monitor.client.Transport"):
            client1 = DjustErrorsClient(dsn="http://fake@host/api/reports/")
            client2 = DjustErrorsClient(dsn="http://fake@host/api/reports/")

        assert len(registered) == 2
        assert registered[0] == client1.flush_exceptions
        assert registered[1] == client2.flush_exceptions

    def test_flush_delegates_to_transport(self):
        """flush_exceptions() calls transport.flush_exceptions with the timeout."""
        client = _make_client()
        client.transport.flush_exceptions = MagicMock()
        client.flush_exceptions(timeout=3.0)
        client.transport.flush_exceptions.assert_called_once_with(timeout=3.0)

    def test_flush_default_timeout(self):
        """flush_exceptions() uses a 5-second default timeout."""
        client = _make_client()
        client.transport.flush_exceptions = MagicMock()
        client.flush_exceptions()
        client.transport.flush_exceptions.assert_called_once_with(timeout=5.0)

    def test_atexit_handler_calls_flush_on_exit(self):
        """Simulated atexit invocation of flush_exceptions calls transport.flush_exceptions."""
        client = _make_client()
        client.transport.flush_exceptions = MagicMock()

        # Simulate atexit by calling the registered function directly.
        client.flush_exceptions()

        client.transport.flush_exceptions.assert_called_once()
