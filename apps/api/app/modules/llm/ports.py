"""LLM provider port. Adapters normalize every provider to this shape (LLM-01, LLM-02)."""

from __future__ import annotations

from typing import Protocol

from app.modules.llm.domain import LLMCheckResult


class LLMProvider(Protocol):
    @property
    def name(self) -> str: ...
    def check(self) -> LLMCheckResult: ...
