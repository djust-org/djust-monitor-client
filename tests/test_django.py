"""Tests for djust_monitor.django helpers not covered by test_middleware.py.

Covers:
- _build_dsn (URL construction from plain key vs full URL)
- _on_full_html_update signal handler (reasons → DJE codes, skip non-actionable)
- _on_liveview_server_error signal handler
- _build_request_context helper
"""

import pytest
from unittest.mock import MagicMock, call, patch

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        INSTALLED_APPS=["django.contrib.contenttypes"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        SECRET_KEY="test-secret-key",
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()

from djust_monitor.django import (
    _build_dsn,
    _build_request_context,
    _on_full_html_update,
    _on_liveview_server_error,
)


# ---------------------------------------------------------------------------
# _build_dsn
# ---------------------------------------------------------------------------


class TestBuildDsn:
    def test_full_url_returned_unchanged(self):
        dsn = "https://mykey@monitor.example.com/api/reports/"
        assert _build_dsn(dsn) == dsn

    def test_http_full_url_returned_unchanged(self):
        dsn = "http://mykey@localhost:8085/api/reports/"
        assert _build_dsn(dsn) == dsn

    def test_plain_key_uses_default_host(self):
        result = _build_dsn("myapikey")
        assert result.startswith("https://")
        assert "myapikey@" in result
        assert "monitor.djust.org" in result
        assert result.endswith("/api/reports/")

    def test_plain_key_uses_custom_host_setting(self):
        from django.test.utils import override_settings
        with override_settings(DJUST_MONITOR_HOST="http://localhost:9000"):
            result = _build_dsn("myapikey")
        assert "myapikey@" in result
        assert "localhost:9000" in result
        assert result.endswith("/api/reports/")

    def test_plain_key_strips_trailing_slash_from_host(self):
        from django.test.utils import override_settings
        with override_settings(DJUST_MONITOR_HOST="http://localhost:9000/"):
            result = _build_dsn("myapikey")
        # Should not have double slash before api/
        assert "localhost:9000/api/reports/" in result

    def test_old_host_setting_fallback(self):
        from django.test.utils import override_settings
        with override_settings(DJUST_ERRORS_HOST="http://legacy:8085"):
            result = _build_dsn("legacykey")
        assert "legacykey@" in result
        assert "legacy:8085" in result


# ---------------------------------------------------------------------------
# _build_request_context
# ---------------------------------------------------------------------------


class TestBuildRequestContext:
    def _make_request(self, method="GET", url="https://example.com/test/",
                      headers=None, user_pk=None):
        request = MagicMock()
        request.method = method
        request.build_absolute_uri.return_value = url
        request.headers = headers or {"Host": "example.com"}
        if user_pk is not None:
            request.user.pk = user_pk
        else:
            del request.user
        return request

    def test_basic_fields(self):
        req = self._make_request()
        ctx = _build_request_context(req)
        assert ctx["method"] == "GET"
        assert ctx["url"] == "https://example.com/test/"
        assert "headers" in ctx

    def test_includes_user_id_when_present(self):
        req = self._make_request(user_pk=99)
        ctx = _build_request_context(req)
        assert ctx["user_id"] == 99

    def test_no_user_id_when_user_absent(self):
        req = self._make_request()  # user deleted from mock
        ctx = _build_request_context(req)
        assert "user_id" not in ctx

    def test_headers_are_dict(self):
        req = self._make_request(headers={"Host": "example.com", "Accept": "text/html"})
        ctx = _build_request_context(req)
        assert isinstance(ctx["headers"], dict)


# ---------------------------------------------------------------------------
# _on_full_html_update signal handler
# ---------------------------------------------------------------------------


class TestOnFullHtmlUpdate:
    """_on_full_html_update should capture DJE events for actionable reasons only."""

    def _call(self, reason, **extra_kwargs):
        """Call the signal handler with default args + overrides."""
        defaults = {
            "sender": None,
            "reason": reason,
            "event_name": "update",
            "view_name": "MyView",
            "html_size": 1000,
            "previous_html_size": 800,
            "patch_count": 5,
            "version": 3,
        }
        defaults.update(extra_kwargs)
        return defaults

    def test_no_patches_emits_DJE053(self):
        with patch("djust_monitor.capture_event") as mock_capture:
            kwargs = self._call("no_patches")
            _on_full_html_update(**kwargs)
            mock_capture.assert_called_once()
            event_type, message = mock_capture.call_args[0]
            assert "DJE-053" in event_type

    def test_no_change_emits_DJE053(self):
        with patch("djust_monitor.capture_event") as mock_capture:
            kwargs = self._call("no_change")
            _on_full_html_update(**kwargs)
            mock_capture.assert_called_once()
            event_type, _ = mock_capture.call_args[0]
            assert "DJE-053" in event_type

    def test_first_render_is_skipped(self):
        with patch("djust_monitor.capture_event") as mock_capture:
            kwargs = self._call("first_render")
            _on_full_html_update(**kwargs)
            mock_capture.assert_not_called()

    def test_component_event_is_skipped(self):
        with patch("djust_monitor.capture_event") as mock_capture:
            kwargs = self._call("component_event")
            _on_full_html_update(**kwargs)
            mock_capture.assert_not_called()

    def test_embedded_child_is_skipped(self):
        with patch("djust_monitor.capture_event") as mock_capture:
            kwargs = self._call("embedded_child")
            _on_full_html_update(**kwargs)
            mock_capture.assert_not_called()

    def test_patch_compression_is_skipped(self):
        with patch("djust_monitor.capture_event") as mock_capture:
            kwargs = self._call("patch_compression")
            _on_full_html_update(**kwargs)
            mock_capture.assert_not_called()

    def test_context_includes_view_name(self):
        with patch("djust_monitor.capture_event") as mock_capture:
            kwargs = self._call("no_patches", view_name="SpecialView")
            _on_full_html_update(**kwargs)
            _, kw = mock_capture.call_args
            assert kw["context"]["view_name"] == "SpecialView"

    def test_context_includes_html_sizes(self):
        with patch("djust_monitor.capture_event") as mock_capture:
            kwargs = self._call("no_patches", html_size=2000, previous_html_size=1500)
            _on_full_html_update(**kwargs)
            _, kw = mock_capture.call_args
            assert kw["context"]["html_size"] == 2000
            assert kw["context"]["previous_html_size"] == 1500

    def test_context_snapshot_included_when_present(self):
        with patch("djust_monitor.capture_event") as mock_capture:
            kwargs = self._call("no_patches", context_snapshot={"x": 1})
            _on_full_html_update(**kwargs)
            _, kw = mock_capture.call_args
            assert kw["context"]["context_snapshot"] == {"x": 1}

    def test_context_snapshot_absent_when_none(self):
        with patch("djust_monitor.capture_event") as mock_capture:
            kwargs = self._call("no_patches")
            _on_full_html_update(**kwargs)
            _, kw = mock_capture.call_args
            assert "context_snapshot" not in kw["context"]

    def test_html_snippet_included_when_present(self):
        with patch("djust_monitor.capture_event") as mock_capture:
            kwargs = self._call("no_patches", html_snippet="<div>...")
            _on_full_html_update(**kwargs)
            _, kw = mock_capture.call_args
            assert kw["context"]["html_snippet"] == "<div>..."

    def test_size_delta_shown_in_message_when_previous_size_known(self):
        with patch("djust_monitor.capture_event") as mock_capture:
            kwargs = self._call("no_patches", html_size=1200, previous_html_size=1000)
            _on_full_html_update(**kwargs)
            _, message = mock_capture.call_args[0]
            assert "→" in message  # delta string present

    def test_size_shown_without_delta_when_no_previous(self):
        with patch("djust_monitor.capture_event") as mock_capture:
            kwargs = self._call("no_patches", html_size=1200, previous_html_size=None)
            _on_full_html_update(**kwargs)
            _, message = mock_capture.call_args[0]
            # No delta arrow when previous size unknown
            assert "→" not in message

    def test_unknown_reason_is_skipped(self):
        """Unknown reasons map to (None, ...) and should be dropped."""
        with patch("djust_monitor.capture_event") as mock_capture:
            kwargs = self._call("totally_unknown_reason")
            _on_full_html_update(**kwargs)
            mock_capture.assert_not_called()


# ---------------------------------------------------------------------------
# _on_liveview_server_error signal handler
# ---------------------------------------------------------------------------


class TestOnLiveviewServerError:
    def test_captures_event(self):
        with patch("djust_monitor.capture_event") as mock_capture:
            _on_liveview_server_error(
                sender=None,
                error="Something went wrong",
                view_name="CounterView",
                context={"event": "increment"},
            )
            mock_capture.assert_called_once()

    def test_event_type_is_liveview_server_error(self):
        with patch("djust_monitor.capture_event") as mock_capture:
            _on_liveview_server_error(sender=None, error="oops", view_name="MyView", context={})
            event_type = mock_capture.call_args[0][0]
            assert event_type == "LiveViewServerError"

    def test_message_is_error_string(self):
        with patch("djust_monitor.capture_event") as mock_capture:
            _on_liveview_server_error(
                sender=None, error="Division by zero", view_name="MyView", context={}
            )
            message = mock_capture.call_args[0][1]
            assert message == "Division by zero"

    def test_context_includes_view(self):
        with patch("djust_monitor.capture_event") as mock_capture:
            _on_liveview_server_error(
                sender=None, error="err", view_name="SpecialView", context={}
            )
            ctx = mock_capture.call_args[1]["context"]
            assert ctx["view"] == "SpecialView"

    def test_context_includes_source_websocket(self):
        with patch("djust_monitor.capture_event") as mock_capture:
            _on_liveview_server_error(
                sender=None, error="err", view_name="MyView", context={}
            )
            ctx = mock_capture.call_args[1]["context"]
            assert ctx["source"] == "websocket"

    def test_extra_context_forwarded(self):
        with patch("djust_monitor.capture_event") as mock_capture:
            _on_liveview_server_error(
                sender=None,
                error="err",
                view_name="MyView",
                context={"event": "click", "session_id": "s-42"},
            )
            ctx = mock_capture.call_args[1]["context"]
            assert ctx["event"] == "click"
            assert ctx["session_id"] == "s-42"

    def test_default_error_message_when_absent(self):
        with patch("djust_monitor.capture_event") as mock_capture:
            _on_liveview_server_error(sender=None)
            message = mock_capture.call_args[0][1]
            assert message == "Unknown LiveView error"

    def test_default_view_name_when_absent(self):
        with patch("djust_monitor.capture_event") as mock_capture:
            _on_liveview_server_error(sender=None)
            ctx = mock_capture.call_args[1]["context"]
            assert ctx["view"] == "unknown"
