"""Identity ports: the membership mirror, token verification, the provider
directory, and webhook verification."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.kernel.errors import DependencyUnavailableError
from app.kernel.tenancy import VerifiedIdentity
from app.kernel.webhooks import EventStatus
from app.modules.identity.domain import ExternalMembership, MembershipRecord


@dataclass(frozen=True, slots=True)
class IdentityWebhookEvent:
    event_id: str
    event_type: str
    data: Mapping[str, object]


class IdentityRepository(Protocol):
    """Users, memberships, and the identity webhook ledger.

    Request transactions (``app_api``) may only read their own rows and
    provision their own user. Writes to memberships and the ledger succeed
    only in system transactions (``app_worker``).
    """

    def find_user_id(self, external_user_id: str) -> UUID | None: ...
    def insert_user(self, external_user_id: str) -> None: ...
    def get_my_membership(self, external_org_id: str) -> MembershipRecord | None: ...
    def get_my_memberships_verified_at(self) -> datetime | None: ...

    def ensure_user(self, external_user_id: str) -> UUID: ...
    def user_exists(self, user_id: UUID) -> bool: ...
    def replace_user_memberships(
        self, user_id: UUID, memberships: Sequence[ExternalMembership], verified_at: datetime
    ) -> bool: ...
    def mark_org_deleted(self, external_org_id: str) -> None: ...
    def remove_user_memberships(self, user_id: UUID, verified_at: datetime) -> None: ...
    def list_users_with_stale_memberships(self, older_than: datetime, limit: int) -> list[tuple[UUID, str]]: ...
    def mark_memberships_checked(self, user_id: UUID, at: datetime) -> None: ...

    def claim_identity_event(self, event: IdentityWebhookEvent) -> EventStatus: ...
    def finish_identity_event(self, event_id: str, status: EventStatus) -> None: ...


class IdentityVerifier(Protocol):
    def verify(self, token: str) -> VerifiedIdentity: ...


class MembershipDirectory(Protocol):
    def list_user_memberships(self, external_user_id: str) -> list[ExternalMembership]:
        """Every membership of the user, or an error: never a partial list.

        Raises ``DependencyUnavailableError`` when the provider cannot be read,
        and ``MembershipListTooLargeError`` when the user has more memberships
        than one read may return.
        """
        ...


class MembershipListTooLargeError(DependencyUnavailableError):
    """The user's memberships exceed what one read may return, so none are applied (fails closed).

    It concerns one user, so it is not a provider outage.
    """

    default_message = "Organization memberships could not be verified."


class IdentityWebhookVerifier(Protocol):
    def verify(self, payload: bytes, headers: Mapping[str, str]) -> IdentityWebhookEvent: ...
