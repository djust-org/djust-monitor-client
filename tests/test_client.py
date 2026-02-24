import pytest
from unittest.mock import MagicMock, patch

from djust_monitor.client import DjustErrorsClient


def _make_client(sample_rate=1.0):
    """Return a client with a mocked Transport."""
    with patch("djust_monitor.client.Transport"):
        return DjustErrorsClient(dsn="http://fake.dsn/1", sample_rate=sample_rate)


class TestSampleRateValidation:
    def test_valid_zero(self):
        client = _make_client(sample_rate=0.0)
        assert client.sample_rate == 0.0

    def test_valid_one(self):
        client = _make_client(sample_rate=1.0)
        assert client.sample_rate == 1.0

    def test_valid_midpoint(self):
        client = _make_client(sample_rate=0.5)
        assert client.sample_rate == 0.5

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="sample_rate"):
            _make_client(sample_rate=-0.1)

    def test_above_one_raises(self):
        with pytest.raises(ValueError, match="sample_rate"):
            _make_client(sample_rate=1.1)

    def test_large_negative_raises(self):
        with pytest.raises(ValueError, match="sample_rate"):
            _make_client(sample_rate=-100.0)

    def test_large_positive_raises(self):
        with pytest.raises(ValueError, match="sample_rate"):
            _make_client(sample_rate=2.0)
