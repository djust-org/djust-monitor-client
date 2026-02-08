import socket
import time

from django.apps import AppConfig
from django.conf import settings
from django.utils import timezone


def _setting(new_name, old_name, default=None):
    """Read a setting by new name, falling back to old name for backwards compat."""
    val = getattr(settings, new_name, None)
    if val is not None:
        return val
    return getattr(settings, old_name, default)


class DjustMonitorConfig(AppConfig):
    name = "djust_monitor"
    verbose_name = "Djust Monitor"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        dsn = _setting("DJUST_MONITOR_DSN", "DJUST_ERRORS_DSN")
        if dsn:
            import djust_monitor

            kwargs = {}
            env = _setting("DJUST_MONITOR_ENVIRONMENT", "DJUST_ERRORS_ENVIRONMENT")
            if env:
                kwargs["environment"] = env
            release = _setting("DJUST_MONITOR_RELEASE", "DJUST_ERRORS_RELEASE")
            if release:
                kwargs["release"] = release
            sample_rate = _setting("DJUST_MONITOR_SAMPLE_RATE", "DJUST_ERRORS_SAMPLE_RATE")
            if sample_rate is not None:
                kwargs["sample_rate"] = sample_rate
            djust_monitor.init(dsn, **kwargs)


# Keep old name as alias for backwards compatibility
DjustErrorsConfig = DjustMonitorConfig

_DEFAULT_IGNORE_PATHS = ["/static/", "/favicon.ico"]


class DjustMonitorMiddleware:
    """Django middleware that captures unhandled exceptions and request metrics."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.metrics_enabled = _setting(
            "DJUST_MONITOR_METRICS_ENABLED", "DJUST_ERRORS_METRICS_ENABLED", True
        )
        self.ignore_paths = _setting(
            "DJUST_MONITOR_METRICS_IGNORE_PATHS",
            "DJUST_ERRORS_METRICS_IGNORE_PATHS",
            _DEFAULT_IGNORE_PATHS,
        )

        # JS error capture auto-injection
        self._js_capture = _setting(
            "DJUST_MONITOR_JS_CAPTURE", "DJUST_ERRORS_JS_CAPTURE", True
        )
        self._dsn = _setting("DJUST_MONITOR_DSN", "DJUST_ERRORS_DSN", "")
        self._environment = _setting(
            "DJUST_MONITOR_ENVIRONMENT", "DJUST_ERRORS_ENVIRONMENT", "production"
        )

        # Cache all capture flags once at init — no per-request settings lookups
        cfg = _setting(
            "DJUST_MONITOR_METRICS_CAPTURE", "DJUST_ERRORS_METRICS_CAPTURE", {}
        )
        self._cap_referrer = cfg.get("referrer", True)
        self._cap_ip = cfg.get("ip_address", True)
        self._cap_user = cfg.get("user_id", True)
        self._cap_ajax = cfg.get("is_ajax", True)
        self._cap_size = cfg.get("response_size", True)
        self._cap_ctype = cfg.get("content_type", True)
        self._cap_view = cfg.get("view_name", True)
        self._cap_db = cfg.get("db_queries", True)
        self._cap_hostname = cfg.get("server_hostname", True)
        self._cap_host = cfg.get("host", True)

        # Cache expensive one-time values
        self._hostname = socket.gethostname() if self._cap_hostname else ""

        # Eagerly import modules used in __call__
        import djust_monitor as _dm
        self._client = _dm
        if self._cap_db:
            from django.db import connection as _conn
            self._connection = _conn
        if self._cap_view:
            from django.urls import resolve as _resolve
            self._resolve = _resolve

    def __call__(self, request):
        if not self.metrics_enabled or self._should_ignore(request.path):
            return self.get_response(request)

        query_count = 0
        query_time = 0.0

        if self._cap_db:
            def _query_wrapper(execute, sql, params, many, context):
                nonlocal query_count, query_time
                query_count += 1
                qstart = time.perf_counter()
                result = execute(sql, params, many, context)
                query_time += (time.perf_counter() - qstart) * 1000
                return result

            with self._connection.execute_wrapper(_query_wrapper):
                start = time.perf_counter()
                response = self.get_response(request)
                duration_ms = (time.perf_counter() - start) * 1000
        else:
            start = time.perf_counter()
            response = self.get_response(request)
            duration_ms = (time.perf_counter() - start) * 1000

        metric = {
            "timestamp": timezone.now().isoformat(),
            "method": request.method,
            "path": request.path,
            "status_code": response.status_code,
            "duration_ms": round(duration_ms, 2),
            "user_agent": request.META.get("HTTP_USER_AGENT", ""),
        }

        if self._cap_referrer:
            metric["referrer"] = request.META.get("HTTP_REFERER", "")

        if self._cap_ip:
            metric["ip_address"] = request.META.get("REMOTE_ADDR")

        if self._cap_user:
            user = getattr(request, "user", None)
            if user is not None and getattr(user, "is_authenticated", False):
                metric["user_id"] = user.pk

        if self._cap_ajax:
            metric["is_ajax"] = (
                request.META.get("HTTP_X_REQUESTED_WITH") == "XMLHttpRequest"
                or "application/json" in request.META.get("HTTP_ACCEPT", "")
            )

        if self._cap_size:
            cl = response.get("Content-Length")
            if cl is not None:
                try:
                    metric["response_size"] = int(cl)
                except (ValueError, TypeError):
                    pass
            elif hasattr(response, "content"):
                metric["response_size"] = len(response.content)

        if self._cap_ctype:
            metric["content_type"] = response.get("Content-Type", "")[:100]

        if self._cap_view:
            try:
                metric["view_name"] = self._resolve(request.path).func.__name__
            except Exception:
                pass

        if self._cap_db:
            metric["db_query_count"] = query_count
            metric["db_query_time_ms"] = round(query_time, 2)

        if self._cap_hostname:
            metric["server_hostname"] = self._hostname

        if self._cap_host:
            try:
                metric["host"] = request.get_host()
            except Exception:
                pass

        # djust LiveView timing breakdown (zero-cost for non-djust apps)
        djust_timing = getattr(request, "_djust_timing", None)
        if djust_timing:
            metric["mount_time_ms"] = djust_timing.get("mount_ms")
            metric["context_time_ms"] = djust_timing.get("context_ms")
            metric["render_time_ms"] = djust_timing.get("render_ms")
            metric["vdom_time_ms"] = djust_timing.get("vdom_ms")

        self._client.record_metric(metric)

        # Auto-inject JS error capture into HTML responses
        if self._js_capture and self._dsn and hasattr(response, "content"):
            content_type = response.get("Content-Type", "")
            if "text/html" in content_type:
                body = response.content.decode(response.charset)
                if "</body>" in body and "data-djust-monitor" not in body:
                    from ._js_capture import build_script_tag

                    tag = build_script_tag(self._dsn, self._environment)
                    response.content = body.replace(
                        "</body>", tag + "</body>"
                    ).encode(response.charset)
                    response["Content-Length"] = len(response.content)

        return response

    def _should_ignore(self, path):
        return any(path.startswith(prefix) for prefix in self.ignore_paths)

    def process_exception(self, request, exception):
        import djust_monitor

        context = _build_request_context(request)
        djust_monitor.capture_exception(exception, context=context)
        return None


# Keep old name as alias for backwards compatibility
DjustErrorsMiddleware = DjustMonitorMiddleware


def _build_request_context(request) -> dict:
    """Extract request context for the error payload."""
    context = {
        "method": request.method,
        "url": request.build_absolute_uri(),
        "headers": dict(request.headers),
    }
    if hasattr(request, "user") and hasattr(request.user, "pk"):
        context["user_id"] = request.user.pk
    return context
