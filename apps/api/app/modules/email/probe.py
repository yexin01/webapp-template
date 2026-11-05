"""Diagnostics probe for Resend: the sending domain, then one email to Resend's test inbox (DEC-015).

The domain check reads the account's domains and sends nothing. The test email goes to
``delivered@resend.dev``, which reaches no person, and counts toward the account's quota; the /integrations page
sends it, and ``pnpm check:setup`` only with ``--send-test-events``.
"""

from __future__ import annotations

import uuid

from app.kernel.diagnostics import IntegrationInfo, Outcome, Verdict
from app.kernel.tenancy import TenantContext
from app.modules.email.domain import RESEND_TEST_DOMAIN, TEST_RECIPIENT, VERIFIED, EmailMessage
from app.modules.email.service import EmailService

_NOT_SENT = "No email was sent: `pnpm check:setup --send-test-events` sends one to " + TEST_RECIPIENT + "."


class EmailProbe:
    def __init__(self, info: IntegrationInfo, email: EmailService, *, send_test_email: bool = True) -> None:
        self._info = info
        self._email = email
        self._send_test_email = send_test_email

    @property
    def info(self) -> IntegrationInfo:
        return self._info

    def check(self, ctx: TenantContext) -> Verdict:
        domain = self._email.sender_domain or ""
        report = self._email.domains()  # a key Resend refuses fails here, before anything is sent
        if domain == RESEND_TEST_DOMAIN:
            return Verdict(
                Outcome.FAILED,
                f"Resend accepted the API key, but EMAIL_FROM is on {RESEND_TEST_DOMAIN}, Resend's testing domain, "
                "which delivers only to your Resend account's own address: verify a domain of yours under Domains "
                "in Resend, then set EMAIL_FROM to an address on it.",
                "testing_domain",
            )
        if report.sending_only_key:
            found = "Resend accepted the API key, which may only send, so the domain of EMAIL_FROM was not read."
        else:
            listed = report.find(domain)
            if listed is None:
                return Verdict(
                    Outcome.FAILED,
                    f"Resend lists no domain {domain} in this account: add it under Domains and verify it, or set "
                    "EMAIL_FROM to an address on a verified domain.",
                    "domain_missing",
                )
            if listed.status != VERIFIED:
                return Verdict(
                    Outcome.FAILED,
                    f"Resend reports the domain {domain} as {listed.status.replace('_', ' ')}: add the DNS records "
                    "it lists under Domains, then verify the domain.",
                    "domain_not_verified",
                )
            if listed.sending is False:
                return Verdict(Outcome.FAILED, f"Sending is turned off for {domain} in Resend.", "sending_disabled")
            found = f"Resend accepted the API key, and {domain} is verified for sending."
        if not self._send_test_email:
            return Verdict(Outcome.PASSED, f"{found} {_NOT_SENT}")
        message = EmailMessage(
            to=(TEST_RECIPIENT,),
            subject="Integration check",
            text="A test email from the /integrations check. Resend's test inbox receives it; nobody reads it.",
            tags=(("source", "integration_check"),),
        )
        email_id = self._email.send(message, idempotency_key=f"integration-check-{uuid.uuid4()}")
        return Verdict(
            Outcome.PASSED,
            f"{found} Resend accepted a test email to {TEST_RECIPIENT}: find it under Emails in the Resend dashboard.",
            reference=email_id,
        )
