"""Synchronize the organization-membership mirror from identity webhooks (DEC-009).

Like billing webhooks, identity events are triggers: membership events cause
a re-read of the user's current memberships from the identity provider, so
out-of-order delivery cannot resurrect a removed membership. Re-reading is
idempotent, which also makes duplicate deliveries harmless.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from app.application.context import ContextResolver
from app.domain.errors import NotConfiguredError
from app.ports import EventStatus, IdentityWebhookEvent, IdentityWebhookVerifier, SystemRepository, UnitOfWork

_MEMBERSHIP_EVENTS = frozenset(
    {
        "organizationMembership.created",
        "organizationMembership.updated",
        "organizationMembership.deleted",
    }
)


class IdentitySyncService:
    def __init__(
        self,
        uow: UnitOfWork,
        verifier: IdentityWebhookVerifier | None,
        resolver: ContextResolver,
    ) -> None:
        self._uow = uow
        self._verifier = verifier
        self._resolver = resolver

    def handle_webhook(self, payload: bytes, headers: Mapping[str, str]) -> EventStatus:
        if self._verifier is None:
            raise NotConfiguredError()
        event = self._verifier.verify(payload, headers)

        with self._uow.as_system() as repo:
            status = repo.claim_identity_event(event)
            if status in (EventStatus.PROCESSED, EventStatus.IGNORED):
                return status
            result, refresh = self._apply(repo, event)

        # The provider call happens after the user row is committed and
        # outside any transaction. If it fails, the event stays "received",
        # the handler returns an error, and the sender retries.
        if refresh is not None:
            self._resolver.refresh_memberships(*refresh)

        with self._uow.as_system() as repo:
            repo.finish_identity_event(event.event_id, result)
        return result

    @staticmethod
    def _apply(repo: SystemRepository, event: IdentityWebhookEvent) -> tuple[EventStatus, tuple[UUID, str] | None]:
        if event.event_type in _MEMBERSHIP_EVENTS:
            external_user_id = _membership_user_id(event.data)
            if external_user_id is None:
                return EventStatus.IGNORED, None
            return EventStatus.PROCESSED, (repo.ensure_user(external_user_id), external_user_id)

        if event.event_type == "organization.deleted":
            external_org_id = _str(event.data.get("id"))
            if external_org_id is None:
                return EventStatus.IGNORED, None
            repo.mark_org_deleted(external_org_id)
            return EventStatus.PROCESSED, None

        if event.event_type == "user.deleted":
            external_user_id = _str(event.data.get("id"))
            user_id = repo.find_user_id(external_user_id) if external_user_id else None
            if user_id is not None:
                # Access is revoked; retention of the user's data is governed
                # by DEC-004 and is not decided here.
                repo.remove_user_memberships(user_id)
            return EventStatus.PROCESSED, None

        return EventStatus.IGNORED, None


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _membership_user_id(data: Mapping[str, object]) -> str | None:
    public = data.get("public_user_data")
    if isinstance(public, Mapping):
        return _str(public.get("user_id"))
    return None
