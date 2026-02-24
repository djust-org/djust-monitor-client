from unittest.mock import MagicMock, patch

import django
from django.conf import settings

# Minimal Django configuration for tests
if not settings.configured:
    settings.configure(
        INSTALLED_APPS=["django.contrib.contenttypes"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        SECRET_KEY="test-secret-key",
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()

import djust_monitor
from djust_monitor.apps import DjustMonitorConfig
from djust_monitor.django import (
    DjustMonitorMiddleware,
    _build_request_context,
)


class TestDjustMonitorMiddleware:
    def test_calls_next_middleware(self):
        get_response = MagicMock(return_value="response")
        middleware = DjustMonitorMiddleware(get_response)
        request = MagicMock()

        result = middleware(request)

        get_response.assert_called_once_with(request)
        assert result == "response"

    @patch("djust_monitor.capture_exception")
    def test_process_exception_captures(self, mock_capture):
        middleware = DjustMonitorMiddleware(lambda r: None)
        request = MagicMock()
        request.method = "GET"
        request.build_absolute_uri.return_value = "https://example.com/test/"
        request.headers = {"Host": "example.com"}
        request.user.pk = 42

        exc = ValueError("test error")
        result = middleware.process_exception(request, exc)

        mock_capture.assert_called_once()
        assert result is None
        # Verify the exception was passed
        call_args = mock_capture.call_args
        assert call_args[0][0] is exc
        # Verify context was built
        ctx = call_args[1]["context"]
        assert ctx["method"] == "GET"
        assert ctx["url"] == "https://example.com/test/"

    @patch("djust_monitor.capture_exception")
    def test_process_exception_returns_none(self, mock_capture):
        middleware = DjustMonitorMiddleware(lambda r: None)
        request = MagicMock()
        request.method = "POST"
        request.build_absolute_uri.return_value = "https://example.com/"
        request.headers = {}

        result = middleware.process_exception(request, RuntimeError("fail"))

        assert result is None


class TestBuildRequestContext:
    def test_includes_method_and_url(self):
        request = MagicMock()
        request.method = "POST"
        request.build_absolute_uri.return_value = "https://example.com/api/"
        request.headers = {"Content-Type": "application/json"}
        request.user.pk = 1

        ctx = _build_request_context(request)

        assert ctx["method"] == "POST"
        assert ctx["url"] == "https://example.com/api/"

    def test_includes_user_id(self):
        request = MagicMock()
        request.method = "GET"
        request.build_absolute_uri.return_value = "/"
        request.headers = {}
        request.user.pk = 99

        ctx = _build_request_context(request)
        assert ctx["user_id"] == 99

    def test_no_user(self):
        request = MagicMock(spec=["method", "build_absolute_uri", "headers"])
        request.method = "GET"
        request.build_absolute_uri.return_value = "/"
        request.headers = {}

        ctx = _build_request_context(request)
        assert "user_id" not in ctx


class TestAppConfigReady:
    @patch("djust_monitor.init")
    def test_init_called_with_dsn(self, mock_init):
        with self.settings(DJUST_MONITOR_DSN="https://key@host/ingest"):
            config = DjustMonitorConfig("djust_monitor", djust_monitor)
            config.ready()

        mock_init.assert_called_once()
        args, kwargs = mock_init.call_args
        assert args[0] == "https://key@host/ingest"

    @patch("djust_monitor.init")
    def test_init_called_with_legacy_dsn(self, mock_init):
        """Backwards compatibility: old DJUST_ERRORS_DSN still works."""
        with self.settings(DJUST_ERRORS_DSN="https://key@host/ingest"):
            config = DjustMonitorConfig("djust_monitor", djust_monitor)
            config.ready()

        mock_init.assert_called_once()
        args, kwargs = mock_init.call_args
        assert args[0] == "https://key@host/ingest"

    @patch("djust_monitor.init")
    def test_no_dsn_skips_init(self, mock_init):
        with self.settings(DJUST_MONITOR_DSN=None):
            config = DjustMonitorConfig("djust_monitor", djust_monitor)
            config.ready()

        mock_init.assert_not_called()

    @patch("djust_monitor.init")
    def test_passes_optional_settings(self, mock_init):
        with self.settings(
            DJUST_MONITOR_DSN="https://key@host/ingest",
            DJUST_MONITOR_ENVIRONMENT="staging",
            DJUST_MONITOR_RELEASE="2.0.0",
            DJUST_MONITOR_SAMPLE_RATE=0.5,
        ):
            config = DjustMonitorConfig("djust_monitor", djust_monitor)
            config.ready()

        _, kwargs = mock_init.call_args
        assert kwargs["environment"] == "staging"
        assert kwargs["release"] == "2.0.0"
        assert kwargs["sample_rate"] == 0.5

    def settings(self, **kwargs):
        """Helper to temporarily override Django settings."""
        from django.test.utils import override_settings
        return override_settings(**kwargs)


class TestJsCaptureInjection:
    """Tests for auto-injection of JS error capture into HTML responses."""

    DSN = "https://testkey123@errors.example.com/api/reports/"

    def _make_html_response(self, body="<html><body><h1>Hi</h1></body></html>"):
        from django.http import HttpResponse
        return HttpResponse(body, content_type="text/html")

    def _make_middleware(self, **setting_overrides):
        from django.test.utils import override_settings
        defaults = {"DJUST_MONITOR_DSN": self.DSN}
        defaults.update(setting_overrides)
        with override_settings(**defaults):
            return DjustMonitorMiddleware(lambda r: None)

    def test_injects_script_into_html(self):
        from django.test.utils import override_settings
        response = self._make_html_response()
        with override_settings(DJUST_MONITOR_DSN=self.DSN):
            mw = DjustMonitorMiddleware(lambda r: response)
            request = MagicMock()
            request.path = "/test/"
            result = mw(request)

        body = result.content.decode()
        assert "data-djust-monitor" in body
        assert '_djeDsn="https://testkey123@errors.example.com/api/reports/"' in body
        assert body.endswith("</body></html>")

    def test_injects_environment(self):
        from django.test.utils import override_settings
        response = self._make_html_response()
        with override_settings(
            DJUST_MONITOR_DSN=self.DSN,
            DJUST_MONITOR_ENVIRONMENT="staging",
        ):
            mw = DjustMonitorMiddleware(lambda r: response)
            request = MagicMock()
            request.path = "/test/"
            result = mw(request)

        body = result.content.decode()
        assert '_djeEnv="staging"' in body

    def test_default_environment_is_production(self):
        from django.test.utils import override_settings
        response = self._make_html_response()
        with override_settings(DJUST_MONITOR_DSN=self.DSN):
            mw = DjustMonitorMiddleware(lambda r: response)
            request = MagicMock()
            request.path = "/test/"
            result = mw(request)

        body = result.content.decode()
        assert '_djeEnv="production"' in body

    def test_no_injection_when_disabled(self):
        from django.test.utils import override_settings
        response = self._make_html_response()
        with override_settings(
            DJUST_MONITOR_DSN=self.DSN,
            DJUST_MONITOR_JS_CAPTURE=False,
        ):
            mw = DjustMonitorMiddleware(lambda r: response)
            request = MagicMock()
            request.path = "/test/"
            result = mw(request)

        body = result.content.decode()
        assert "data-djust-monitor" not in body

    def test_no_injection_without_dsn(self):
        from django.test.utils import override_settings
        response = self._make_html_response()
        with override_settings(DJUST_MONITOR_DSN=""):
            mw = DjustMonitorMiddleware(lambda r: response)
            request = MagicMock()
            request.path = "/test/"
            result = mw(request)

        body = result.content.decode()
        assert "data-djust-monitor" not in body

    def test_no_injection_for_json_response(self):
        from django.http import JsonResponse
        from django.test.utils import override_settings
        response = JsonResponse({"ok": True})
        with override_settings(DJUST_MONITOR_DSN=self.DSN):
            mw = DjustMonitorMiddleware(lambda r: response)
            request = MagicMock()
            request.path = "/test/"
            result = mw(request)

        body = result.content.decode()
        assert "data-djust-monitor" not in body

    def test_no_double_injection(self):
        from django.test.utils import override_settings
        # Simulate a page that already has the script (e.g., via template tag)
        html = '<html><body><script data-djust-monitor>existing</script></body></html>'
        response = self._make_html_response(html)
        with override_settings(DJUST_MONITOR_DSN=self.DSN):
            mw = DjustMonitorMiddleware(lambda r: response)
            request = MagicMock()
            request.path = "/test/"
            result = mw(request)

        body = result.content.decode()
        assert body.count("data-djust-monitor") == 1

    def test_no_injection_without_body_tag(self):
        from django.test.utils import override_settings
        response = self._make_html_response("<html><h1>Fragment</h1></html>")
        with override_settings(DJUST_MONITOR_DSN=self.DSN):
            mw = DjustMonitorMiddleware(lambda r: response)
            request = MagicMock()
            request.path = "/test/"
            result = mw(request)

        body = result.content.decode()
        assert "data-djust-monitor" not in body

    def test_updates_content_length(self):
        from django.test.utils import override_settings
        response = self._make_html_response()
        with override_settings(DJUST_MONITOR_DSN=self.DSN):
            mw = DjustMonitorMiddleware(lambda r: response)
            request = MagicMock()
            request.path = "/test/"
            result = mw(request)

        assert "data-djust-monitor" in result.content.decode()
        assert int(result["Content-Length"]) == len(result.content)


class TestProxyCircuitBreaker:
    """Circuit breaker for _proxy_report / _forward."""

    DSN = "https://testkey123@errors.example.com/api/reports/"

    def _make_middleware(self):
        from django.test.utils import override_settings
        with override_settings(DJUST_MONITOR_DSN=self.DSN):
            return DjustMonitorMiddleware(lambda r: None)

    def _make_proxy_request(self, body=None):
        request = MagicMock()
        request.path = "/_djust_monitor/reports/"
        request.method = "POST"
        request.body = (body or b'{"error": "test"}')
        return request

    def test_proxy_accepted_normally(self):
        mw = self._make_middleware()
        request = self._make_proxy_request()
        response = mw(request)
        assert response.status_code == 202

    def test_circuit_opens_after_max_failures(self):
        import time
        import threading
        mw = self._make_middleware()
        # Simulate 5 consecutive failures by setting state directly
        with mw._proxy_lock:
            mw._proxy_failures = 5
            mw._proxy_backoff_until = time.monotonic() + 60.0

        request = self._make_proxy_request()
        threads_spawned = []
        original_thread = threading.Thread
        def tracking_thread(*args, **kwargs):
            t = original_thread(*args, **kwargs)
            threads_spawned.append(t)
            return t

        with patch("djust_monitor.django.threading.Thread", side_effect=tracking_thread):
            response = mw(request)

        # Circuit is open — no thread should have been spawned
        assert len(threads_spawned) == 0
        assert response.status_code == 202

    def test_circuit_allows_retry_after_backoff_expires(self):
        import time
        mw = self._make_middleware()
        # Circuit is open but backoff already expired
        with mw._proxy_lock:
            mw._proxy_failures = 5
            mw._proxy_backoff_until = time.monotonic() - 1.0  # already past

        request = self._make_proxy_request()
        with patch("djust_monitor.django._requests") as mock_requests:
            mock_requests.post.return_value = MagicMock(status_code=202)
            mw._proxy_report(request)
            import time as _time
            _time.sleep(0.05)  # let daemon thread run

    def test_failure_increments_counter_and_sets_backoff(self):
        import time
        import threading
        mw = self._make_middleware()
        assert mw._proxy_failures == 0

        done = threading.Event()
        original_lock_class = type(mw._proxy_lock)

        with patch("djust_monitor.django._requests") as mock_requests:
            def fail_and_signal(*args, **kwargs):
                raise Exception("connection refused")
            mock_requests.post.side_effect = fail_and_signal

            # Patch Thread to track when our thread finishes
            original_thread = threading.Thread
            threads = []
            def tracking_thread(*args, **kwargs):
                t = original_thread(*args, **kwargs)
                threads.append(t)
                return t
            with patch("djust_monitor.django.threading.Thread", side_effect=tracking_thread):
                mw._proxy_report(self._make_proxy_request())

        # Wait for the spawned thread to complete
        for t in threads:
            t.join(timeout=2.0)

        assert mw._proxy_failures == 1
        assert mw._proxy_backoff_until > time.monotonic()

    def test_success_resets_failure_counter(self):
        import time
        import threading
        mw = self._make_middleware()
        # Start with some failures
        with mw._proxy_lock:
            mw._proxy_failures = 3
            mw._proxy_backoff_until = time.monotonic() - 1.0

        threads = []
        original_thread = threading.Thread
        def tracking_thread(*args, **kwargs):
            t = original_thread(*args, **kwargs)
            threads.append(t)
            return t

        with patch("djust_monitor.django._requests") as mock_requests:
            mock_requests.post.return_value = MagicMock(status_code=202)
            with patch("djust_monitor.django.threading.Thread", side_effect=tracking_thread):
                mw._proxy_report(self._make_proxy_request())

        for t in threads:
            t.join(timeout=2.0)

        assert mw._proxy_failures == 0


class TestBuildScriptTag:
    def test_contains_dsn(self):
        from djust_monitor._js_capture import build_script_tag
        tag = build_script_tag("https://key@host/api/")
        assert 'var _djeDsn="https://key@host/api/"' in tag
        assert 'data-djust-monitor' in tag

    def test_contains_environment(self):
        from djust_monitor._js_capture import build_script_tag
        tag = build_script_tag("https://key@host/api/", "staging")
        assert '_djeEnv="staging"' in tag

    def test_contains_iife(self):
        from djust_monitor._js_capture import build_script_tag
        tag = build_script_tag("https://key@host/api/")
        assert "(function(){" in tag
        assert "})();" in tag

    def test_wraps_in_script_tag(self):
        from djust_monitor._js_capture import build_script_tag
        tag = build_script_tag("https://key@host/api/")
        assert tag.startswith("<script data-djust-monitor>")
        assert tag.endswith("</script>")
