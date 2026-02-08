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

import djust_errors_client
from djust_errors_client.django import (
    DjustErrorsConfig,
    DjustErrorsMiddleware,
    _build_request_context,
)


class TestDjustErrorsMiddleware:
    def test_calls_next_middleware(self):
        get_response = MagicMock(return_value="response")
        middleware = DjustErrorsMiddleware(get_response)
        request = MagicMock()

        result = middleware(request)

        get_response.assert_called_once_with(request)
        assert result == "response"

    @patch("djust_errors_client.capture_exception")
    def test_process_exception_captures(self, mock_capture):
        middleware = DjustErrorsMiddleware(lambda r: None)
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

    @patch("djust_errors_client.capture_exception")
    def test_process_exception_returns_none(self, mock_capture):
        middleware = DjustErrorsMiddleware(lambda r: None)
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
    @patch("djust_errors_client.init")
    def test_init_called_with_dsn(self, mock_init):
        with self.settings(DJUST_ERRORS_DSN="https://key@host/ingest"):
            config = DjustErrorsConfig("djust_errors_client", djust_errors_client)
            config.ready()

        mock_init.assert_called_once()
        args, kwargs = mock_init.call_args
        assert args[0] == "https://key@host/ingest"

    @patch("djust_errors_client.init")
    def test_no_dsn_skips_init(self, mock_init):
        with self.settings(DJUST_ERRORS_DSN=None):
            config = DjustErrorsConfig("djust_errors_client", djust_errors_client)
            config.ready()

        mock_init.assert_not_called()

    @patch("djust_errors_client.init")
    def test_passes_optional_settings(self, mock_init):
        with self.settings(
            DJUST_ERRORS_DSN="https://key@host/ingest",
            DJUST_ERRORS_ENVIRONMENT="staging",
            DJUST_ERRORS_RELEASE="2.0.0",
            DJUST_ERRORS_SAMPLE_RATE=0.5,
        ):
            config = DjustErrorsConfig("djust_errors_client", djust_errors_client)
            config.ready()

        _, kwargs = mock_init.call_args
        assert kwargs["environment"] == "staging"
        assert kwargs["release"] == "2.0.0"
        assert kwargs["sample_rate"] == 0.5

    def settings(self, **kwargs):
        """Helper to temporarily override Django settings."""
        from django.test.utils import override_settings
        return override_settings(**kwargs)
