"""Application settings: the kernel's core settings plus each module's settings,
validated together (ENV-02 to ENV-05).

To add a module's settings, inherit its settings class here and add its
``*_problems`` function to ``_validate``. A module whose calls hold request
threads until a provider answers must add its concurrency bound to
``reserved_request_threads``.
"""

from __future__ import annotations

from typing import Self

from pydantic import AliasChoices, ValidationError, model_validator

from app.kernel.infrastructure.config import ConfigError, CoreSettings, core_problems
from app.kernel.infrastructure.db import REQUEST_THREADS
from app.modules.billing.settings import BillingSettings, billing_problems
from app.modules.diagnostics.settings import DiagnosticsSettings
from app.modules.identity.settings import IdentitySettings, authorized_parties, identity_problems
from app.modules.llm.settings import LLMSettings, llm_problems

# The fewest request threads that must stay free for threads waiting for a database connection.
MIN_WAITER_PLACES = 8
# Request threads a Clerk key-set lookup may hold until Clerk answers: the fetch and its capped waiters.
CLERK_KEY_SET_THREADS = 5


class Settings(CoreSettings, IdentitySettings, BillingSettings, LLMSettings, DiagnosticsSettings):
    @model_validator(mode="after")
    def _validate(self) -> Self:
        self.clerk_authorized_parties = authorized_parties(self, self)
        problems = [
            *core_problems(self),
            *identity_problems(self, self),
            *billing_problems(self, self),
            *llm_problems(self, self),
            *thread_budget_problems(self),
        ]
        if problems:
            raise ValueError("; ".join(problems))
        return self


def reserved_request_threads(s: Settings) -> int:
    """The request threads that bounded external work may hold at once, each until its provider answers.

    LLM checks; checkout and portal calls to Stripe; Clerk membership reads,
    with the requests waiting for one; billing synchronizations, which hold
    all but one worker connection across Stripe calls (BILL-10); and, while
    session tokens are verified with keys fetched from Clerk (CLERK_JWT_KEY
    empty), key-set lookups. The database keeps these threads out of its
    connection-waiter budget.

    Work that no concurrency limit caps is not counted (see ``Database``):
    operator diagnostics, cron job runs, and key-set lookups while no usable
    key set is cached.
    """
    return (
        s.llm_max_concurrent_checks
        + s.billing_max_concurrent_stripe_calls
        + s.clerk_max_concurrent_reads
        + (s.database_worker_pool_max_size - 1)
        + (CLERK_KEY_SET_THREADS if _fetches_clerk_keys(s) else 0)
    )


def _fetches_clerk_keys(s: Settings) -> bool:
    """Whether the identity module verifies session tokens with keys it fetches from Clerk."""
    return s.clerk_issuer is not None and bool(s.clerk_authorized_parties) and not s.clerk_jwt_key


def thread_budget_problems(s: Settings) -> list[str]:
    """Admitted scoped requests and bounded external work must leave threads for connection waiters."""
    left = REQUEST_THREADS - s.database_pool_max_size - reserved_request_threads(s)
    if left >= MIN_WAITER_PLACES:
        return []
    key_set = (
        f", with the {CLERK_KEY_SET_THREADS} threads kept for Clerk key-set lookups while CLERK_JWT_KEY is empty,"
        if _fetches_clerk_keys(s)
        else " together"
    )
    return [
        "DATABASE_POOL_MAX_SIZE, DATABASE_WORKER_POOL_MAX_SIZE, LLM_MAX_CONCURRENT_CHECKS, "
        f"BILLING_MAX_CONCURRENT_STRIPE_CALLS and CLERK_MAX_CONCURRENT_READS{key_set} leave "
        f"{max(left, 0)} of the {REQUEST_THREADS} request threads for threads waiting for a database "
        f"connection, fewer than {MIN_WAITER_PLACES}: lower them by {MIN_WAITER_PLACES - left} in total"
    ]


def _setting_names() -> set[str]:
    """The keywords ``Settings`` binds: each field's name and the names of its aliases."""
    names = set(Settings.model_fields)
    for field in Settings.model_fields.values():
        alias = field.validation_alias
        choices = alias.choices if isinstance(alias, AliasChoices) else [alias]
        names.update(choice for choice in choices if isinstance(choice, str))
    return names


def load_settings(**overrides: object) -> Settings:
    """Load settings, converting validation errors into name-only messages.

    A keyword that names no setting is refused: the model ignores unknown keywords, so a
    misspelled one would leave its setting at the default.
    """
    if unknown := sorted(k for k in overrides if not k.startswith("_") and k not in _setting_names()):
        raise TypeError(f"load_settings() got unknown settings: {', '.join(unknown)}")
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        details: list[str] = []
        for err in exc.errors(include_input=False, include_url=False):
            loc = ".".join(str(part) for part in err["loc"]).upper()
            msg = str(err["msg"]).removeprefix("Value error, ")
            details.append(f"{loc}: {msg}" if loc else msg)
        raise ConfigError("Invalid configuration: " + " | ".join(details)) from None
