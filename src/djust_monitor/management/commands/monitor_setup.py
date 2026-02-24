"""Create (or retrieve) this project's entry on monitor.djust.org."""

import os

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from djust_monitor.django import _build_dsn
from djust_monitor.transport import parse_dsn


def _get_config():
    raw_dsn = getattr(settings, "DJUST_MONITOR_DSN", "") or os.environ.get("DJUST_MONITOR_DSN", "")
    admin_key = (
        getattr(settings, "DJUST_MONITOR_ADMIN_KEY", "")
        or os.environ.get("DJUST_MONITOR_ADMIN_KEY", "")
    )
    if not admin_key:
        raise CommandError(
            "DJUST_MONITOR_ADMIN_KEY is not configured. "
            "Set it in Django settings or as an environment variable."
        )

    # Derive base URL from DSN if available, otherwise fall back to default host
    if raw_dsn:
        endpoint, _ = parse_dsn(_build_dsn(raw_dsn))
        base_url = endpoint.replace("/api/reports/", "")
    else:
        base_url = os.environ.get("DJUST_MONITOR_HOST", "https://monitor.djust.org").rstrip("/")

    return base_url, admin_key


class Command(BaseCommand):
    help = "Create this project on monitor.djust.org and print its DSN"

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="Project name to register")
        parser.add_argument("--org-slug", required=True, help="Organization slug to assign the project to")
        parser.add_argument("--write-env", metavar="FILE", help="Append DJUST_MONITOR_DSN=... to this file (e.g. .env or ~/.secrets)")

    def handle(self, *args, **options):
        base_url, admin_key = _get_config()

        resp = requests.post(
            f"{base_url}/api/admin/projects/create/",
            json={"name": options["name"], "org_slug": options["org_slug"]},
            headers={"Authorization": f"Bearer {admin_key}"},
            timeout=10,
        )

        if resp.status_code == 409:
            raise CommandError(
                f"Project '{options['name']}' already exists in org '{options['org_slug']}'. "
                "Check monitor.djust.org for the existing DSN."
            )

        if not resp.ok:
            raise CommandError(f"Failed to create project ({resp.status_code}): {resp.text}")

        project = resp.json()["project"]
        dsn = project["dsn"]

        self.stdout.write(self.style.SUCCESS(f"\nProject created: {project['name']} (id={project['id']})"))
        self.stdout.write(f"  DSN: {dsn}\n")

        if options["write_env"]:
            path = os.path.expanduser(options["write_env"])
            with open(path, "a") as f:
                f.write(f'\nexport DJUST_MONITOR_DSN="{dsn}"\n')
            self.stdout.write(self.style.SUCCESS(f"  DSN appended to {path}"))
        else:
            self.stdout.write("  Add to your settings or secrets file:")
            self.stdout.write(f'  export DJUST_MONITOR_DSN="{dsn}"')
            self.stdout.write("")
