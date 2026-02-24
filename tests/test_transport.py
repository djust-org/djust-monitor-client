"""Tests for transport._safe_serialize and Transport serialization safety."""
import json
from unittest.mock import MagicMock, patch

import pytest

from djust_monitor.transport import Transport, _safe_serialize


# ---------------------------------------------------------------------------
# _safe_serialize unit tests
# ---------------------------------------------------------------------------


class _Unserializable:
    """Object that cannot be JSON-serialized normally."""

    def __repr__(self):
        return "<Unserializable>"


def test_safe_serialize_plain_dict():
    payload = {"event": "error", "level": "error", "count": 3}
    result = json.loads(_safe_serialize(payload))
    assert result == payload


def test_safe_serialize_non_serializable_value():
    """Non-JSON-serializable values fall back to str()."""
    obj = _Unserializable()
    payload = {"context": {"model_instance": obj}}
    result = json.loads(_safe_serialize(payload))
    assert result["context"]["model_instance"] == str(obj)


def test_safe_serialize_circular_reference():
    """Circular references must not raise — they fall back to str() via default."""
    a: dict = {}
    b: dict = {"ref": a}
    a["ref"] = b  # circular

    # json.dumps without default raises ValueError for circular refs
    # Our _safe_serialize should handle it gracefully
    raw = _safe_serialize({"data": a})
    result = json.loads(raw)
    # Either the payload was serialized (unlikely for true circles) or we get
    # the last-resort fallback envelope.
    assert isinstance(result, dict)


def test_safe_serialize_exception_with_context_cycle():
    """Exception chains can create __context__ cycles; ensure no crash."""
    try:
        try:
            raise ValueError("original")
        except ValueError as exc:
            raise RuntimeError("wrapper") from exc
    except RuntimeError as exc:
        # Manually create a cycle
        exc.__context__ = exc  # type: ignore[assignment]
        payload = {"exception": exc}
        raw = _safe_serialize(payload)
        result = json.loads(raw)
        assert isinstance(result, dict)


def test_safe_serialize_nested_non_serializable():
    """Nested non-serializable values at arbitrary depth are handled."""

    class CustomObj:
        pass

    payload = {"a": {"b": {"c": CustomObj()}}}
    result = json.loads(_safe_serialize(payload))
    assert isinstance(result["a"]["b"]["c"], str)


# ---------------------------------------------------------------------------
# Transport._do_send / _do_send_sync serialization safety (integration)
# ---------------------------------------------------------------------------


DSN = "https://testkey@monitor.example.com/api/reports/"


def _make_transport() -> Transport:
    return Transport(dsn=DSN)


def _mock_response(status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    return resp


def test_do_send_does_not_raise_on_non_serializable():
    """Transport._do_send() must not silently drop events with bad context."""
    t = _make_transport()
    payload = {"context": {"bad": _Unserializable()}}
    with patch("djust_monitor.transport.requests.post", return_value=_mock_response()) as mock_post:
        t._do_send(t.endpoint, payload)
        assert mock_post.called
        sent = json.loads(mock_post.call_args.kwargs["data"])
        assert "context" in sent


def test_do_send_sync_does_not_raise_on_non_serializable():
    """Transport._do_send_sync() must not raise on non-serializable payload."""
    t = _make_transport()
    payload = {"context": {"bad": _Unserializable()}}
    with patch("djust_monitor.transport.requests.post", return_value=_mock_response()) as mock_post:
        result = t._do_send_sync(t.endpoint, payload)
        assert result is True
        sent = json.loads(mock_post.call_args.kwargs["data"])
        assert "context" in sent


def test_do_send_circular_ref_does_not_drop_event():
    """Events with circular-ref context are still delivered (not silently dropped)."""
    t = _make_transport()
    circ: dict = {}
    circ["self"] = circ
    with patch("djust_monitor.transport.requests.post", return_value=_mock_response()) as mock_post:
        t._do_send(t.endpoint, {"ctx": circ})
        assert mock_post.called
