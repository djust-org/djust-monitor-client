from djust_monitor.fingerprint import (
    _normalize_message,
    compute_fingerprint,
)


class TestNormalizeMessage:
    def test_hex_addresses(self):
        msg = "object at 0x7f1a2b3c4d5e is not callable"
        assert "<addr>" in _normalize_message(msg)
        assert "0x7f1a2b3c4d5e" not in _normalize_message(msg)

    def test_numeric_ids(self):
        msg = "User 12345 not found"
        assert "<id>" in _normalize_message(msg)

    def test_short_numbers_preserved(self):
        msg = "expected 2 arguments"
        assert _normalize_message(msg) == "expected 2 arguments"

    def test_uuids(self):
        msg = "Record a1b2c3d4-e5f6-7890-abcd-ef1234567890 not found"
        assert "<uuid>" in _normalize_message(msg)

    def test_timestamps(self):
        msg = "Error at 2026-02-07T12:00:00"
        assert "<timestamp>" in _normalize_message(msg)


class TestComputeFingerprint:
    def test_returns_hex_string(self):
        payload = {
            "exception": {
                "type": "ValueError",
                "message": "bad value",
                "frames": [
                    {"file": "/app/views.py", "function": "index"},
                ],
            }
        }
        fp = compute_fingerprint(payload)
        assert len(fp) == 64  # SHA-256 hex
        assert all(c in "0123456789abcdef" for c in fp)

    def test_same_error_same_fingerprint(self):
        payload1 = {
            "exception": {
                "type": "ValueError",
                "message": "invalid literal",
                "frames": [{"file": "/app/views.py", "function": "process"}],
            }
        }
        payload2 = {
            "exception": {
                "type": "ValueError",
                "message": "invalid literal",
                "frames": [{"file": "/app/views.py", "function": "process"}],
            }
        }
        assert compute_fingerprint(payload1) == compute_fingerprint(payload2)

    def test_different_type_different_fingerprint(self):
        base = {
            "exception": {
                "type": "ValueError",
                "message": "bad",
                "frames": [{"file": "/app/views.py", "function": "x"}],
            }
        }
        other = {
            "exception": {
                "type": "TypeError",
                "message": "bad",
                "frames": [{"file": "/app/views.py", "function": "x"}],
            }
        }
        assert compute_fingerprint(base) != compute_fingerprint(other)

    def test_variable_parts_normalized(self):
        payload1 = {
            "exception": {
                "type": "ValueError",
                "message": "User 12345 not found",
                "frames": [{"file": "/app/views.py", "function": "get_user"}],
            }
        }
        payload2 = {
            "exception": {
                "type": "ValueError",
                "message": "User 67890 not found",
                "frames": [{"file": "/app/views.py", "function": "get_user"}],
            }
        }
        assert compute_fingerprint(payload1) == compute_fingerprint(payload2)

    def test_empty_frames(self):
        payload = {
            "exception": {
                "type": "RuntimeError",
                "message": "oops",
                "frames": [],
            }
        }
        fp = compute_fingerprint(payload)
        assert len(fp) == 64

    def test_skips_site_packages(self):
        payload = {
            "exception": {
                "type": "ValueError",
                "message": "bad",
                "frames": [
                    {"file": "/app/views.py", "function": "my_view"},
                    {
                        "file": "/usr/lib/python3.12/site-packages/django/core/handlers.py",
                        "function": "handle",
                    },
                ],
            }
        }
        fp = compute_fingerprint(payload)
        # Should use /app/views.py:my_view as top app frame (last non-library frame)
        assert len(fp) == 64
