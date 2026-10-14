"""Maintenance commands: ``python -m app.cli <command>``.

Destructive and development-only commands refuse non-local databases
(ENV-05). Reconciliation is a dry run unless ``--apply`` is given (BILL-19).
``job <name>`` runs any registered job (the same ones Vercel Cron calls).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

from app.kernel.infrastructure import migrations
from app.kernel.infrastructure.config import ConfigError, load_database_url
from app.settings import load_settings


def _database_url(args: argparse.Namespace) -> str:
    url = load_database_url()
    if not migrations.is_local_database(url) and not getattr(args, "allow_remote", False):
        raise migrations.UnsafeTargetError(
            "DATABASE_URL is not local. Re-run with --allow-remote only with explicit owner authorization."
        )
    return url


def cmd_migrate(args: argparse.Namespace) -> int:
    applied = migrations.migrate(_database_url(args))
    print(json.dumps({"applied": applied}))
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    migrations.seed(_database_url(args))
    print(json.dumps({"seeded": True}))
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    if not args.yes:
        print("Refusing to reset without --yes. This drops all local application data.", file=sys.stderr)
        return 2
    url = _database_url(args)
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
                "divergences": [d.__dict__ | {"user_id": str(d.user_id)} for d in report.divergences],
            },
            indent=2,
        )
    )
    return 0


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("migrate", help="Apply pending migrations")
    p.add_argument(
        "--allow-remote", action="store_true", help="Permit a non-local DATABASE_URL (requires authorization)"
    )
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("seed", help="Load deterministic synthetic data (local only)")
    p.set_defaults(func=cmd_seed)

    p = sub.add_parser("reset", help="Drop and recreate the local schema (local only)")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--seed", action="store_true")
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("reconcile", help="Compare billing records with Stripe (dry run by default)")
    p.add_argument("--user", help="Internal user UUID to reconcile")
    p.add_argument("--apply", action="store_true")
    p.set_defaults(func=cmd_reconcile)

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


if __name__ == "__main__":
    raise SystemExit(main())
