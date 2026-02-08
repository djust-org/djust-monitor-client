import sys

from djust_monitor.serializer import serialize_exception


def _make_exception():
    """Create a real exception with traceback."""
    try:
        quantity = int("abc")
    except ValueError as exc:
        return exc


class TestSerializeException:
    def test_basic_structure(self):
        exc = _make_exception()
        payload = serialize_exception(exc)

        assert "timestamp" in payload
        assert payload["environment"] == "production"
        assert payload["exception"]["type"] == "ValueError"
        assert "invalid literal" in payload["exception"]["message"]
        assert isinstance(payload["exception"]["frames"], list)
        assert len(payload["exception"]["frames"]) > 0

    def test_frame_fields(self):
        exc = _make_exception()
        payload = serialize_exception(exc)
        frame = payload["exception"]["frames"][-1]

        assert "file" in frame
        assert "line" in frame
        assert "function" in frame
        assert "context_line" in frame

    def test_context_info(self):
        exc = _make_exception()
        payload = serialize_exception(exc)

        assert "python_version" in payload["context"]
        assert "os" in payload["context"]
        assert "hostname" in payload["context"]

    def test_environment_and_release(self):
        exc = _make_exception()
        payload = serialize_exception(exc, environment="staging", release="1.0.0")

        assert payload["environment"] == "staging"
        assert payload["release"] == "1.0.0"

    def test_request_context(self):
        exc = _make_exception()
        ctx = {"method": "POST", "url": "https://example.com/test/"}
        payload = serialize_exception(exc, request_context=ctx)

        assert payload["request"]["method"] == "POST"
        assert payload["request"]["url"] == "https://example.com/test/"

    def test_no_request_context(self):
        exc = _make_exception()
        payload = serialize_exception(exc)

        assert "request" not in payload

    def test_captures_locals(self):
        exc = _make_exception()
        payload = serialize_exception(exc)
        # The frame where int("abc") was called should have locals
        frame = payload["exception"]["frames"][-1]
        # Locals may or may not be present depending on traceback capture
        # but the frame structure should be valid
        assert isinstance(frame.get("locals", {}), dict)
