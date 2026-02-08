"""Django AppConfig for djust-monitor — auto-discovered by Django 3.2+."""

import logging

from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)


def _setting(new_name, old_name, default=None):
    val = getattr(settings, new_name, None)
    if val is not None:
        return val
    return getattr(settings, old_name, default)


class DjustMonitorConfig(AppConfig):
    name = "djust_monitor"
    verbose_name = "Djust Monitor"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        raw_dsn = _setting("DJUST_MONITOR_DSN", "DJUST_ERRORS_DSN")
        if not raw_dsn:
            return

        import djust_monitor
        from djust_monitor.django import _build_dsn

        dsn = _build_dsn(raw_dsn)

        kwargs = {}
        env = _setting("DJUST_MONITOR_ENVIRONMENT", "DJUST_ERRORS_ENVIRONMENT")
        if env:
            kwargs["environment"] = env
        release = _setting("DJUST_MONITOR_RELEASE", "DJUST_ERRORS_RELEASE")
        if release:
            kwargs["release"] = release
        sample_rate = _setting(
            "DJUST_MONITOR_SAMPLE_RATE", "DJUST_ERRORS_SAMPLE_RATE"
        )
        if sample_rate is not None:
            kwargs["sample_rate"] = sample_rate

        # Shorter flush interval in DEBUG mode for faster feedback
        if getattr(settings, "DEBUG", False):
            kwargs.setdefault("metrics_flush_interval", 10.0)
            kwargs.setdefault("metrics_batch_size", 5)

        djust_monitor.init(dsn, **kwargs)

        # Connect to djust's full_html_update signal (if djust is installed)
        try:
            from djust.signals import full_html_update
            from djust_monitor.django import _on_full_html_update

            full_html_update.connect(_on_full_html_update)
            logger.debug("djust-monitor: connected to full_html_update signal")
        except ImportError:
            pass  # djust not installed — signal not available
