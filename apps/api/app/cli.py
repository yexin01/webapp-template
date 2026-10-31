"""Maintenance commands: ``python -m app.cli <command>``.

Destructive and development-only commands refuse non-local databases, need
``APP_ENV`` set to local or test, and need ``--yes`` (ENV-05). Reconciliation
is a dry run unless ``--apply`` is given (BILL-19).
``migrate``, ``seed``, ``reset``, and ``api-login`` connect as the schema owner:
``MIGRATION_DATABASE_URL``, or ``DATABASE_URL`` when it is not set.
``setup-stripe`` changes only a Stripe sandbox and prints the .env lines to set.
``check-config`` only loads the settings; ``diagnose`` runs the integration probes without signing in (see app.doctor).
``job <name>`` runs any registered job (the same ones Vercel Cron calls).
"""

from __future__ import annotations

import argparse
import dataclasses
import getpass
import json
import os
import re
import sys
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.conninfo import conninfo_to_dict

from app.kernel.errors import DependencyUnavailableError
from app.kernel.infrastructure import migrations
from app.kernel.infrastructure.config import (
    ConfigError,
    load_database_url,
    load_migration_database_url,
    require_development_environment,
    secret,
)
from app.kernel.infrastructure.db import DATABASE_UNAVAILABLE
from app.settings import load_settings


def _database_url(args: argparse.Namespace) -> str:
    url = load_migration_database_url()
    if not migrations.is_local_database(url) and not getattr(args, "allow_remote", False):
        raise migrations.UnsafeTargetError(
            "The database is not local. Re-run with --allow-remote only with explicit owner authorization."
        )
    return url


def cmd_migrate(args: argparse.Namespace) -> int:
    applied = migrations.migrate(_database_url(args))
    print(json.dumps({"applied": applied}))
    return 0


def _login_password() -> str:
    """The runtime login's password: ``API_DB_PASSWORD``, else typed twice. Never echoed or written anywhere."""
    password = os.environ.get("API_DB_PASSWORD", "")
    if password:
        return password
    if not sys.stdin.isatty():
        raise ConfigError("Set API_DB_PASSWORD, or run this command in a terminal to type the password.")
    password = getpass.getpass(f"New password for {migrations.APP_LOGIN_ROLE}: ")
    if getpass.getpass("Repeat it: ") != password:
        raise ConfigError("The two passwords differ.")
    return password


def cmd_api_login(args: argparse.Namespace) -> int:
    url = _database_url(args)
    migrations.enable_api_login(url, _login_password(), migrations.APP_LOGIN_ROLE)
    print(json.dumps({"login_role": migrations.APP_LOGIN_ROLE, "login": True}))
    template = migrations.runtime_url_template(url, migrations.APP_LOGIN_ROLE)
    where = f"postgresql://{migrations.APP_LOGIN_ROLE}:<password>@<same host, port, and database>"
    pooler = (
        f" Port {migrations.TRANSACTION_POOLER_PORT} is Supabase's Transaction pooler, which the API needs; "
        "the Session pooler (port 5432) is for migrations."
        if template and migrations.is_supabase_pooler(url)
        else ""
    )
    print(
        f"{migrations.APP_LOGIN_ROLE} can log in. In the environment where the API runs, set DATABASE_URL to "
        f"{template or where} (percent-encode special characters in the password).{pooler} "
        "Keep MIGRATION_DATABASE_URL out of that environment.",
        file=sys.stderr,
    )
    return 0


def _confirmed(args: argparse.Namespace, refusal: str) -> bool:
    """ENV-05: destructive commands run only with an explicit ``--yes``, checked after the target guards."""
    if not args.yes:
        print(refusal, file=sys.stderr)
    return bool(args.yes)


def cmd_seed(args: argparse.Namespace) -> int:
    require_development_environment("seed synthetic data")
    url = _database_url(args)
    if not _confirmed(args, "Refusing to seed without --yes. This writes synthetic rows into the local database."):
        return 2
    migrations.seed(url)
    print(json.dumps({"seeded": True}))
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    require_development_environment("reset the database")
    url = _database_url(args)
    if not _confirmed(args, "Refusing to reset without --yes. This drops all local application data."):
        return 2
    migrations.reset(url)
    migrations.migrate(url)
    if args.seed:
        migrations.seed(url)
    print(json.dumps({"reset": True, "seeded": bool(args.seed)}))
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    from app.bootstrap import bootstrap

    container = bootstrap()
    try:
        report = container.billing.service.reconcile(user_id=UUID(args.user) if args.user else None, apply=args.apply)
    finally:
        container.close()
    print(
        json.dumps(
            {
                "applied": report.applied,
                "customers_checked": report.customers_checked,
                "divergences": [dataclasses.asdict(d) | {"user_id": str(d.user_id)} for d in report.divergences],
                "unlinked_customers": report.unlinked_customers,
                "rejected_customers": report.rejected_customers,
            },
            indent=2,
        )
    )
    return 0


def cmd_setup_stripe(args: argparse.Namespace) -> int:
    """Create the Pro product and prices in a Stripe sandbox, then print the .env lines (it never writes .env)."""
    from app.modules.billing.adapters import stripe_setup

    require_development_environment("set up Stripe")
    key = secret(stripe_setup.StripeSetupSettings().stripe_secret_key)
    try:
        stripe_setup.run(key, stripe_setup.Amounts(args.currency, args.monthly, args.yearly))
    except stripe_setup.SetupError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def cmd_check_config(args: argparse.Namespace) -> int:
    from app import doctor

    return doctor.check_config(args.env_file)


def cmd_diagnose(args: argparse.Namespace) -> int:
    from app import doctor

    return doctor.diagnose(args.env_file, args.send_test_events)


def cmd_job(args: argparse.Namespace) -> int:
    from app.bootstrap import bootstrap

    container = bootstrap()
    try:
        if args.name is None:
            jobs = [
                {"name": j.name, "schedule": j.schedule, "description": j.description}
                for j in container.kernel.jobs.all()
            ]
            print(json.dumps(jobs, indent=2))
            return 0
        job = container.kernel.jobs.get(args.name)
        if job is None:
            print(f"Unknown job: {args.name}", file=sys.stderr)
            return 2
        summary = dict(job.run())
    finally:
        container.close()
    print(json.dumps({"job": args.name, "summary": summary}))
    return 0


def cmd_openapi(args: argparse.Namespace) -> int:
    """Export the OpenAPI document without connecting to any service."""
    from app.bootstrap import build_container, create_app
    from app.modules.billing.adapters.fakes import InMemoryBillingProvider

    settings = load_settings(
        _env_file=None,
        app_env="test",
        database_url="postgresql://unused:unused@127.0.0.1:1/unused",
        stripe_price_pro_monthly="price_placeholder_monthly",
        stripe_price_pro_yearly="price_placeholder_yearly",
    )
    app = create_app(build_container(settings, billing_provider=InMemoryBillingProvider()))
    document = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
    Path(args.output).write_text(document, encoding="utf-8")
    print(json.dumps({"written": args.output}))
    return 0


def _database_down(command: str) -> str:
    """One line for a database that cannot be reached: where, and what to do. Never the credentials."""
    runtime = command in ("reconcile", "job")  # these connect as the API does, the others as the schema owner
    try:
        params = conninfo_to_dict(load_database_url() if runtime else load_migration_database_url())
        port = params.get("port")
        where = f" at {params.get('host') or 'localhost'}{f':{port}' if port else ''}"
    except (ConfigError, psycopg.Error):
        where = ""
    variables = "DATABASE_URL" if runtime else "MIGRATION_DATABASE_URL or DATABASE_URL"
    return (
        f"Cannot connect to the database{where}. Start the local one with `pnpm db:up`; "
        f"otherwise check {variables} in .env."
    )


def _currency(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z]{3}", value):
        raise argparse.ArgumentTypeError("must be a three-letter ISO 4217 code such as usd")
    return value.lower()


def _amount(value: str) -> int:
    if not value.isdigit() or int(value) < 1:
        raise argparse.ArgumentTypeError("must be a whole number of minor units (cents) of at least 1")
    return int(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("migrate", help="Apply pending migrations")
    p.add_argument(
        "--allow-remote", action="store_true", help="Permit a non-local DATABASE_URL (requires authorization)"
    )
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser(
        "api-login",
        help="Let the app_login role log in with a password (typed, or API_DB_PASSWORD); the server gets only a hash",
    )
    p.add_argument("--allow-remote", action="store_true", help="Permit a non-local database (requires authorization)")
    p.set_defaults(func=cmd_api_login)

    p = sub.add_parser("seed", help="Load deterministic synthetic data (local only)")
    p.add_argument("--yes", action="store_true", help="Confirm writing into the local database")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("reset", help="Drop and recreate the local schema (local only)")
    p.add_argument("--yes", action="store_true", help="Confirm dropping all local application data")
    p.add_argument("--seed", action="store_true")
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("reconcile", help="Compare billing records with Stripe (dry run by default)")
    p.add_argument("--user", help="Internal user UUID to reconcile")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_reconcile)

    p = sub.add_parser(
        "setup-stripe",
        help="Create the Pro product and its two prices in a Stripe sandbox (local only; never writes .env)",
    )
    p.add_argument("--currency", required=True, type=_currency, help="ISO 4217 code, for example usd")
    p.add_argument("--monthly", required=True, type=_amount, help="Monthly price in minor units (cents)")
    p.add_argument("--yearly", required=True, type=_amount, help="Yearly price in minor units (cents)")
    p.set_defaults(func=cmd_setup_stripe)

    p = sub.add_parser("check-config", help="Validate the settings and print each integration's mode (no network)")
    p.add_argument("--env-file", help="Read this file instead of the repository .env")
    p.set_defaults(func=cmd_check_config)

    p = sub.add_parser(
        "diagnose",
        help="Run the /integrations checks without signing in (not in production); prints an evidence block",
    )
    p.add_argument("--env-file", help="Read this file instead of the repository .env")
    p.add_argument(
        "--send-test-events",
        action="store_true",
        help="Also send one PostHog event and one Sentry issue (sandbox projects only)",
    )
    p.set_defaults(func=cmd_diagnose)

    p = sub.add_parser("job", help="Run a registered job, or list jobs when no name is given")
    p.add_argument("name", nargs="?")
    p.set_defaults(func=cmd_job)

    p = sub.add_parser("openapi", help="Write the OpenAPI document")
    p.add_argument("--output", required=True)
    p.set_defaults(func=cmd_openapi)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ConfigError, migrations.UnsafeTargetError, migrations.MigrationError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except psycopg.OperationalError as exc:
        if exc.sqlstate is not None:  # the server answered (a lock timeout, a deadlock): not a missing connection
            raise
        print(_database_down(args.command), file=sys.stderr)
        return 1
    except DependencyUnavailableError as exc:
        if exc.public_message != DATABASE_UNAVAILABLE:  # a provider outage keeps its traceback
            raise
        print(_database_down(args.command), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
