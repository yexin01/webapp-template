"""Who may use diagnostics. They expose configuration and call external
services, so they are an operator tool, not a user feature."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OperatorPolicy:
    operator_ids: frozenset[str]
    allow_any_signed_in_user: bool = False

    def permits(self, external_user_id: str) -> bool:
        return self.allow_any_signed_in_user or external_user_id in self.operator_ids
