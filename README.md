# djust-monitor

Error tracking and performance monitoring client for Django applications. Captures unhandled exceptions, request metrics, and client-side JavaScript errors — then sends them to your [djust Monitor](https://monitor.djust.org) dashboard.

## Installation

```bash
pip install djust-monitor
```

## Setup

### 1. Create a project

Each project gets its own API key. Create one from your [djust Monitor dashboard](https://monitor.djust.org).

### 2. Configure Django settings

Add the app, middleware, and DSN to your `settings.py`:

```python
# settings.py

INSTALLED_APPS = [
    ...
    "djust_monitor",
]

MIDDLEWARE = [
    ...
    "djust_monitor.django.DjustMonitorMiddleware",
]

DJUST_MONITOR_DSN = "https://<your-api-key>@monitor.djust.org/api/reports/"
```

The middleware should go near the end of the list so it can measure the full request lifecycle. Place it after authentication middleware but before any custom analytics middleware.

### 3. Verify it works

Start your application. The SDK auto-initializes on Django startup (via `AppConfig.ready()`) and will:

- Capture unhandled exceptions with full stack traces
- Record request metrics (duration, status code, DB queries, etc.)
- Auto-inject a `<script>` tag into HTML responses for client-side JS error capture

## DSN Format

The DSN (Data Source Name) embeds your API key in the URL:

```
https://<api-key>@monitor.djust.org/api/reports/
```

You can also pass a plain API key and set the host separately:

```python
DJUST_MONITOR_DSN = "your-api-key-here"
DJUST_MONITOR_HOST = "https://monitor.djust.org"  # default
```

For production, use an environment variable:

```python
import os
DJUST_MONITOR_DSN = os.environ.get("DJUST_MONITOR_DSN", "")
```

## Settings Reference

| Setting | Default | Description |
|---------|---------|-------------|
| `DJUST_MONITOR_DSN` | `""` | DSN with API key (required) |
| `DJUST_MONITOR_ENVIRONMENT` | `"production"` | Environment tag (e.g. `"staging"`, `"dev"`) |
| `DJUST_MONITOR_RELEASE` | `""` | Release/version tag |
| `DJUST_MONITOR_SAMPLE_RATE` | `1.0` | Error sampling rate (0.0 to 1.0) |
| `DJUST_MONITOR_METRICS_ENABLED` | `True` | Enable request metrics collection |
| `DJUST_MONITOR_METRICS_IGNORE_PATHS` | `["/static/", "/favicon.ico", "/_djust_monitor/"]` | Paths to exclude from metrics |
| `DJUST_MONITOR_JS_CAPTURE` | `True` | Auto-inject JS error capture into HTML responses |
| `DJUST_MONITOR_METRICS_CAPTURE` | `{}` | Fine-grained control over which metric fields to capture (see below) |

### Metrics Capture Options

Control individual metric fields via `DJUST_MONITOR_METRICS_CAPTURE`:

```python
DJUST_MONITOR_METRICS_CAPTURE = {
    "referrer": True,        # HTTP Referer header
    "ip_address": True,      # Client IP
    "user_id": True,         # Authenticated user PK
    "is_ajax": True,         # XHR/fetch detection
    "response_size": True,   # Response Content-Length
    "content_type": True,    # Response Content-Type
    "view_name": True,       # Resolved Django view name
    "db_queries": True,      # DB query count and timing
    "server_hostname": True, # Server hostname
    "host": True,            # Request Host header
}
```

## Features

### Exception Capture

Unhandled exceptions are automatically captured by the middleware's `process_exception` hook. You can also capture exceptions manually:

```python
import djust_monitor

try:
    risky_operation()
except Exception as e:
    djust_monitor.capture_exception(e, context={"user_id": 42})
```

### Request Metrics

Every request is timed and annotated with method, path, status code, duration, DB query count/time, and more. Metrics are batched and flushed in the background to avoid impacting request latency.

### JS Error Capture

The middleware auto-injects a small inline `<script>` tag before `</body>` in HTML responses. This captures `window.onerror` and `unhandledrejection` events and forwards them through a same-origin proxy endpoint (`/_djust_monitor/reports/`), avoiding CORS issues.

Disable with `DJUST_MONITOR_JS_CAPTURE = False`.

### djust LiveView Integration

When used alongside the [djust framework](https://djust.org), the SDK automatically connects to djust's `full_html_update` signal to report performance anomalies (e.g., full HTML re-renders that should have been incremental VDOM patches).

### Sensitive Data Scrubbing

All payloads are scrubbed before transmission. Keys matching common sensitive patterns (`password`, `secret`, `token`, `api_key`, `authorization`, `cookie`, `session`, `credit_card`, `ssn`) have their values replaced with `[Filtered]`.

## Programmatic API

```python
import djust_monitor

# Capture an exception
djust_monitor.capture_exception(exc, context={"key": "value"})

# Capture a custom event
djust_monitor.capture_event("SlowQuery", "Query took 5.2s", context={"sql": "..."})

# Record a custom metric
djust_monitor.record_metric({"path": "/api/data", "duration_ms": 150})

# Flush buffered metrics immediately
djust_monitor.flush_metrics()
```

## Backwards Compatibility

If migrating from `djust-errors`, all settings with `DJUST_ERRORS_*` prefix are still supported as fallbacks. The old middleware class name `DjustErrorsMiddleware` is also available as an alias.

## License

MIT
