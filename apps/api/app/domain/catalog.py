"""Server-side allowlist of purchasable prices (BILL-03)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.entitlements import PlanKey


@dataclass(frozen=True, slots=True)
class PriceCatalog:
    prices: Mapping[PlanKey, str]

    def price_for(self, key: PlanKey) -> str | None:
        return self.prices.get(key)

    def key_for(self, price_id: str | None) -> PlanKey:
        for key, value in self.prices.items():
            if value == price_id:
                return key
        return PlanKey.UNKNOWN
