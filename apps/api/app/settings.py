"""Application settings: the kernel's core settings plus each module's settings,
validated together (ENV-02 to ENV-05).

To add a module's settings, inherit its settings class here and add its
``*_problems`` function to ``_validate``.
"""

from __future__ import annotations

from typing import Self

from pydantic import ValidationError, model_validator

from app.kernel.infrastructure.config import ConfigError, CoreSettings, core_problems
from app.modules.billing.settings import BillingSettings, billing_problems
from app.modules.diagnostics.settings import DiagnosticsSettings
from app.modules.identity.settings import IdentitySettings, identity_problems
from app.modules.llm.settings import LLMSettings, llm_problems


class Settings(CoreSettings, IdentitySettings, BillingSettings, LLMSettings, DiagnosticsSettings):
    @model_validator(mode="after")
    def _validate(self) -> Self:
        problems = [
            *core_problems(self),
            *identity_problems(self, self),
            *billing_problems(self, self),
            *llm_problems(self, self),
        ]
        if problems:
            raise ValueError("; ".join(problems))
        return self


def load_settings(**overrides: object) -> Settings:
    """Load settings, converting validation errors into name-only messages."""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        details: list[str] = []
        for err in exc.errors(include_input=False, include_url=False):
            loc = ".".join(str(part) for part in err["loc"]).upper()
            msg = str(err["msg"]).removeprefix("Value error, ")
            details.append(f"{loc}: {msg}" if loc else msg)
        raise ConfigError("Invalid configuration: " + " | ".join(details)) from None
