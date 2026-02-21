"""Show errors from monitor.djust.org via the admin API."""

import os

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from djust_monitor.django import _build_dsn
from djust_monitor.transport import parse_dsn


def _get_config():
    """Return (base_url, admin_key) from Django settings / env vars."""
    raw_dsn = getattr(settings, "DJUST_MONITOR_DSN", "") or os.environ.get("DJUST_MONITOR_DSN", "")
    if not raw_dsn:
        raise CommandError(
            "DJUST_MONITOR_DSN is not configured. "
            "Set it in Django settings or as an environment variable."
        )
    endpoint, _ = parse_dsn(_build_dsn(raw_dsn))
    # Derive base URL from the reports endpoint
    base_url = endpoint.replace("/api/reports/", "")

    admin_key = (
        getattr(settings, "DJUST_MONITOR_ADMIN_KEY", "")
        or os.environ.get("DJUST_MONITOR_ADMIN_KEY", "")
    )
    if not admin_key:
        raise CommandError(
            "DJUST_MONITOR_ADMIN_KEY is not configured. "
            "Set it in Django settings or as an environment variable."
        )

    return base_url, admin_key


class Command(BaseCommand):
    help = "Show errors from monitor.djust.org"

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Include resolved errors")
        parser.add_argument("--project", help="Filter by project name (partial match)")
        parser.add_argument("--environment", help="Filter by environment (e.g. production)")
        parser.add_argument("--days", type=int, help="Only show errors from the last N days")
        parser.add_argument("--limit", type=int, default=50, help="Max errors to show (default: 50)")

    def handle(self, *args, **options):
        base_url, admin_key = _get_config()

        params = {"limit": options["limit"]}
        if not options["all"]:
            params["include_resolved"] = "false"
        if options["project"]:
            params["project"] = options["project"]
        if options["environment"]:
            params["environment"] = options["environment"]
        if options["days"]:
            params["days"] = options["days"]

        try:
            resp = requests.get(
                f"{base_url}/api/admin/errors/",
                params=params,
                headers={"Authorization": f"Bearer {admin_key}"},
                timeout=10,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise CommandError(f"Failed to fetch errors: {e}") from e

        data = resp.json()
        errors = data.get("errors", [])
        total = data.get("count", 0)

        if not errors:
            self.stdout.write(self.style.SUCCESS("No errors found."))
            return

        # Header
        self.stdout.write("")
        self.stdout.write(
            f"  {'ID':>4}  {'Project':<20}  {'Env':<12}  {'Count':>5}  "
            f"{'R':1}  {'Type':<35}  Message"
        )
        self.stdout.write("  " + "-" * 120)

        for e in errors:
            resolved = self.style.SUCCESS("✓") if e["is_resolved"] else " "
            exc_type = e["exception_type"][:35]
            message = e["exception_message"][:70] if e["exception_message"] else ""
            last_seen = e["last_seen"][:16].replace("T", " ")
            self.stdout.write(
                f"  {e['id']:>4}  {e['project']:<20}  {e['environment']:<12}  "
                f"{e['occurrence_count']:>5}  {resolved}  {exc_type:<35}  {message}"
            )

        self.stdout.write("")
        shown = len(errors)
        filter_desc = ""
        if options["project"]:
            filter_desc += f" project={options['project']}"
        if options["environment"]:
            filter_desc += f" env={options['environment']}"
        self.stdout.write(f"  {shown} of {total} errors shown{filter_desc}")
        self.stdout.write("")
