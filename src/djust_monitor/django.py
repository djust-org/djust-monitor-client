import json
import logging
import socket
import threading
import time

import requests as _requests
from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone

logger = logging.getLogger(__name__)


def _setting(new_name, old_name, default=None):
    """Read a setting by new name, falling back to old name for backwards compat."""
    val = getattr(settings, new_name, None)
    if val is not None:
        return val
    return getattr(settings, old_name, default)


def _build_dsn(raw_dsn):
    """Build a full DSN URL from a raw DSN value.

    Accepts either:
      - Full URL:  "http://API_KEY@host:port/api/reports/"
      - Plain key: "API_KEY"  (requires DJUST_MONITOR_HOST setting)

    When a plain key is given, DJUST_MONITOR_HOST (default "http://localhost:8085")
    is used to construct the full DSN.
    """
    if "://" in raw_dsn:
        return raw_dsn

    # Plain API key — build URL from host setting
    host = _setting("DJUST_MONITOR_HOST", "DJUST_ERRORS_HOST", "https://monitor.djust.org")
    host = host.rstrip("/")
    return f"{host.replace('://', f'://{raw_dsn}@')}/api/reports/"


def _on_full_html_update(sender, **kwargs):
    """Handle djust's full_html_update signal — forward to monitor as an event."""
    import djust_monitor

    reason = kwargs.get("reason", "unknown")
    event_name = kwargs.get("event_name", "unknown")
    view_name = kwargs.get("view_name", "unknown")
    html_size = kwargs.get("html_size", 0)
    previous_html_size = kwargs.get("previous_html_size")
    patch_count = kwargs.get("patch_count")
    version = kwargs.get("version", 0)
    context_snapshot = kwargs.get("context_snapshot")
    html_snippet = kwargs.get("html_snippet")
    previous_html_snippet = kwargs.get("previous_html_snippet")

    # Build size delta string
    if previous_html_size is not None:
        delta = html_size - previous_html_size
        sign = "+" if delta >= 0 else ""
        size_info = f"{previous_html_size:,}B → {html_size:,}B ({sign}{delta:,}B)"
    else:
        size_info = f"{html_size:,}B"

    # Map reasons to DJE error codes and explanations
    reasons = {
        "first_render": (
            None,  # Normal operation, not an error
            "First render (mount) — no previous VDOM exists to diff against. "
            "This is normal and expected for the initial page load."
        ),
        "no_patches": (
            "DJE-053",
            "The Rust VDOM engine diffed the previous and current render but "
            "produced no patches. The template structure likely changed "
            "significantly. See: https://djust.org/errors/DJE-053"
        ),
        "component_event": (
            None,  # Known limitation, not actionable
            "Component events use a separate VDOM from the parent view. "
            "Per-component VDOM tracking is not yet implemented."
        ),
        "embedded_child": (
            None,  # By design
            "Embedded child views always receive full HTML because they "
            "render independently from the parent's VDOM tree."
        ),
        "patch_compression": (
            None,  # Optimization, not an error
            f"The VDOM engine generated {patch_count} patches, but the full "
            f"HTML was >30% smaller than the patch payload. "
            "Sending HTML instead for better network performance."
        ),
        "no_change": (
            "DJE-053",
            "The event handler modified state outside the <div data-djust-root> "
            "boundary. Consider using push_event with _skip_render = True. "
            "See: https://djust.org/errors/DJE-053"
        ),
    }
    error_code, explanation = reasons.get(reason, (None, f"Unknown reason: {reason}"))

    # Skip reporting non-actionable events (first_render, component_event, etc.)
    if error_code is None:
        return

    # Error titles for display (matches seed_errors catalog)
    _ERROR_TITLES = {
        "DJE-053": "Full HTML update — state outside VDOM root",
    }
    title = _ERROR_TITLES.get(error_code, "Full HTML update")
    event_type = f"{error_code}: {title}"

    message = (
        f"Full HTML update on {view_name} (event: {event_name}, "
        f"v{version}, {size_info}). {explanation}"
    )

    ctx = {
        "error_code": error_code,
        "view_name": view_name,
        "event_name": event_name,
        "reason": reason,
        "html_size": html_size,
        "previous_html_size": previous_html_size,
        "patch_count": patch_count,
        "vdom_version": version,
    }
    # Include diagnostic fields when present (DJE-053 no_change / no_patches)
    if context_snapshot is not None:
        ctx["context_snapshot"] = context_snapshot
    if html_snippet:
        ctx["html_snippet"] = html_snippet
    if previous_html_snippet:
        ctx["previous_html_snippet"] = previous_html_snippet

    djust_monitor.capture_event(event_type, message, context=ctx)


def _on_liveview_server_error(sender, **kwargs):
    """Handle djust's liveview_server_error signal — forward to monitor."""
    import djust_monitor

    error = kwargs.get("error", "Unknown LiveView error")
    view_name = kwargs.get("view_name", "unknown")
    context = kwargs.get("context", {})

    djust_monitor.capture_event(
        "LiveViewServerError",
        error,
        context={
            "view": view_name,
            "source": "websocket",
            **context,
        },
    )


_PROXY_PATH = "/_djust_monitor/reports/"
_DEFAULT_IGNORE_PATHS = ["/static/", "/favicon.ico", "/_djust_monitor/"]


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
        raw_dsn = _setting("DJUST_MONITOR_DSN", "DJUST_ERRORS_DSN", "")
        self._dsn = _build_dsn(raw_dsn) if raw_dsn else ""
        self._environment = _setting(
            "DJUST_MONITOR_ENVIRONMENT", "DJUST_ERRORS_ENVIRONMENT", "production"
        )

        # Same-origin proxy: parse DSN into endpoint + API key for forwarding
        self._proxy_endpoint = ""
        self._proxy_api_key = ""
        if self._dsn:
            from .transport import parse_dsn

            try:
                self._proxy_endpoint, self._proxy_api_key = parse_dsn(self._dsn)
            except ValueError:
                logger.warning("djust-monitor: invalid DSN, JS proxy disabled")

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

        # Circuit breaker for the JS error proxy (_proxy_report / _forward)
        self._proxy_failures = 0
        self._proxy_backoff_until = 0.0
        self._proxy_max_failures = 5
        self._proxy_max_backoff = 60.0
        self._proxy_lock = threading.Lock()

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
        # Same-origin proxy for JS error reports — intercept before normal processing
        if request.path == _PROXY_PATH and request.method == "POST":
            return self._proxy_report(request)

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

    def _proxy_report(self, request):
        """Forward a JS error report to the monitor server (same-origin proxy).

        CSRF exempt by design: this endpoint receives fire-and-forget XHR POSTs
        from the injected JS capture script. No cookies or sessions are used —
        authentication is added server-side via the configured API key.
        """
        if not self._proxy_endpoint:
            return JsonResponse({"error": "Monitor not configured"}, status=503)

        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        endpoint = self._proxy_endpoint
        api_key = self._proxy_api_key

        # Check circuit breaker before spawning a thread
        now = time.monotonic()
        with self._proxy_lock:
            if (
                self._proxy_failures >= self._proxy_max_failures
                and now < self._proxy_backoff_until
            ):
                remaining = self._proxy_backoff_until - now
                logger.debug(
                    "djust-monitor: proxy circuit open, dropping JS report "
                    "(backoff %.0fs remaining)",
                    remaining,
                )
                return JsonResponse({"status": "accepted"}, status=202)

        def _forward():
            try:
                _requests.post(
                    endpoint,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=5.0,
                )
                with self._proxy_lock:
                    self._proxy_failures = 0
            except Exception:
                logger.debug("djust-monitor: proxy forward failed", exc_info=True)
                with self._proxy_lock:
                    self._proxy_failures += 1
                    backoff = min(2 ** self._proxy_failures, self._proxy_max_backoff)
                    self._proxy_backoff_until = time.monotonic() + backoff

        threading.Thread(target=_forward, daemon=True).start()
        return JsonResponse({"status": "accepted"}, status=202)

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
