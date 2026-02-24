# Changelog

All notable changes to **djust-monitor-client** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased] — feat/circuit-breaker-atexit-flush

### Added
- **Circuit breaker** on the HTTP transport — stops hammering a down monitor
  server; auto-recovers after a configurable cooldown period
  (`failure_threshold=5`, `cooldown=60s` by default).
- **Exponential backoff** with retries (up to 3 attempts, capped at 10 s) for
  transient 5xx and network errors on both async and sync sends.
- **`atexit` flush** — in-flight exception sends are joined on clean process
  exit so reports queued just before shutdown are not silently discarded.
- `DjustErrorsClient.flush_exceptions(timeout)` public method.
- `Transport.send_sync()` for blocking sends (critical pipeline events).
- Thread-local **context management** API (`set_context`, `push_context`,
  `pop_context`, `clear_context`, `scoped_context`) for attaching pipeline /
  agent metadata to all captures in a thread.

---

## [0.1.0] — 2026-02-20

### Added
- **`liveview_server_error` signal handler** — captures LiveView server errors
  forwarded via `djust.signals.liveview_server_error` and sends them to the
  monitor as `LiveViewServerError` events.
- **`monitor_setup` management command** — checks configuration and prints a
  summary of active settings.
- **`monitor_errors` management command** — lists captured errors via the
  monitor API.
- **Circuit breaker on JS error proxy** in `DjustMonitorMiddleware` — prevents
  cascading failures when the upstream monitor server is unavailable.

---

## [0.0.9] — 2026-02-12

### Added
- **`BatchingHTTPHandler`** — Python `logging.Handler` that ships log records
  to `monitor.djust.org` in configurable batches with a circuit breaker and
  queue-overflow protection.

---

## [0.0.8] — 2026-02-10

### Docs
- Added comprehensive README with setup instructions, settings reference,
  and integration examples.

---

## [0.0.7] — 2026-02-09

### Fixed
- Default SDK host changed from `http://localhost:8085` to
  `https://monitor.djust.org` so out-of-the-box installs point at the
  production service.
- Capture DJE-053 diagnostic fields (`context_snapshot`, `html_snippet`,
  `previous_html_snippet`) in monitor reports.

---

## [0.0.6] — 2026-02-08

### Fixed
- Include descriptive title alongside DJE error code in `event_type` field
  (e.g. `DJE-053: Full HTML update — state outside VDOM root`).
- Map `full_html_update` signal `reason` values to the correct DJE error codes.

### Added
- `capture_event()` public API for non-exception events.
- **Same-origin JS proxy** — `/_djust_monitor/reports/` endpoint in middleware
  forwards JS error reports to the monitor server, avoiding CORS issues.
  The DSN is no longer embedded in the injected client script.
- `full_html_update` signal handler with `no_change` reason support.

---

## [0.0.5] — 2026-02-08

### Changed
- Renamed HTML attribute from `data-djust-errors` to `data-djust-monitor`.
- Rebranded package from `djust-errors-client` to `djust-monitor`.

---

## [0.0.4] — 2026-02-08

### Added
- Auto-inject JS error capture `<script>` tag into HTML responses via
  `DjustMonitorMiddleware`.

---

## [0.0.1] — 2026-02-08

### Added
- Initial SDK: `DjustErrorsClient` with `capture()`, serializer, fingerprinter,
  scrubber, and HTTP transport.
- `DjustMonitorMiddleware` for unhandled exception capture and request metrics.
- `DjustMonitorConfig` Django app with `ready()` auto-init from settings.
- Auto-detect release from `git describe --tags --always` or
  `DJUST_MONITOR_RELEASE` environment variable.
- `sample_rate` validation (must be in `[0.0, 1.0]`).
