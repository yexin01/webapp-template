"""Email through Resend (DEC-015): the message rules, the sandbox recipients, the settings, the service, and the
adapter against Resend's documented wire format (a mock transport; no request leaves the process)."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from app.kernel.errors import (
    DependencyUnavailableError,
    InvalidRequestError,
    NotConfiguredError,
    ProviderRejectedError,
    RateLimitedError,
)
from app.kernel.infrastructure.config import ConfigError
from app.modules.email import adapters
from app.modules.email.adapters import USER_AGENT, MockEmailSender, ResendEmailSender
from app.modules.email.domain import (
    TEST_RECIPIENT,
    DomainReport,
    EmailMessage,
    RecipientPolicy,
    SendingDomain,
    message_problems,
    sender_address,
)
from app.modules.email.module import _policy, _sender, describe
from app.modules.email.service import EmailService
from tests.conftest import make_settings

KEY = "re_" + "k" * 24
FROM = "Example <noreply@example.com>"
MESSAGE = EmailMessage(to=("ada@example.org",), subject="Welcome", text="Hello.")


def _replace(message: EmailMessage, **change: Any) -> EmailMessage:
    values = {f: getattr(message, f) for f in ("to", "subject", "text", "html", "reply_to", "tags")}
    return EmailMessage(**{**values, **change})


# --- addresses, messages and recipients ------------------------------------------------------------------

SENDERS = [
    ("noreply@example.com", "noreply@example.com"),
    ("Example <noreply@example.com>", "noreply@example.com"),
    ("  Example Team <hello+news@mail.example.co.uk>  ", "hello+news@mail.example.co.uk"),
    ("noreply@example", None),  # no top-level domain
    ("Example noreply@example.com", None),  # a name without brackets
    ("<noreply@example.com", None),
    ("no reply@example.com", None),
    ('"Ex" <noreply@example.com>', None),  # quotes are refused rather than half-understood
    ("Example <noreply@example.com>\r\nBcc: x@evil.example", None),  # header injection
    ("", None),
]


@pytest.mark.parametrize(("sender", "address"), SENDERS)
def test_the_sender_forms_resend_accepts(sender: str, address: str | None) -> None:
    assert sender_address(sender) == address


@pytest.mark.parametrize(("sender", "address"), SENDERS)
def test_the_settings_accept_exactly_the_senders_the_runtime_parses(sender: str, address: str | None) -> None:
    """settings.py keeps its own copy of the grammar (a module's settings import only kernel config)."""
    if not sender.strip():
        return
    if address is None:
        with pytest.raises(ConfigError, match="EMAIL_FROM must be an address"):
            make_settings(email_from=sender)
    else:
        assert make_settings(email_from=sender).email_from == sender


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"to": ()}, "1 to 50 recipients"),
        ({"to": tuple(f"user{i}@example.org" for i in range(51))}, "1 to 50 recipients"),
        ({"to": ("not-an-address",)}, "a recipient is not an email address"),
        ({"reply_to": "nobody"}, "the reply-to address is not an email address"),
        ({"subject": "  "}, "the subject is one line"),
        ({"subject": "Hi\nBcc: x@evil.example"}, "the subject is one line"),
        ({"subject": "x" * 999}, "the subject is one line"),
        ({"text": " "}, "the plain-text part is empty"),
        ({"tags": (("source", "a b"),)}, "a tag's name and value"),
        ({"tags": (("", "x"),)}, "a tag's name and value"),
    ],
)
def test_a_message_resend_would_refuse_is_refused_first(change: dict[str, Any], problem: str) -> None:
    message = _replace(MESSAGE, **change)
    problems = message_problems(message, "key-1")
    assert any(problem in p for p in problems), problems


def test_a_sendable_message_has_no_problems_and_keys_are_bounded() -> None:
    assert message_problems(_replace(MESSAGE, tags=(("source", "signup_2"),), reply_to="help@example.com"), "k") == []
    assert message_problems(MESSAGE, "") != []
    assert message_problems(MESSAGE, "k" * 257) != []
    assert message_problems(MESSAGE, "k" * 256) == []


def test_the_sandbox_allows_only_resends_test_inboxes_and_the_listed_recipients() -> None:
    policy = RecipientPolicy.sandbox(["me@example.org", "@team.example", " "])
    assert policy.permits(TEST_RECIPIENT)
    assert policy.permits("delivered+signup@resend.dev")  # Resend's labels
    assert policy.permits("bounced@RESEND.dev")
    assert policy.permits("Me@Example.org")
    assert policy.permits("anyone@team.example")
    assert not policy.permits("someone@example.org")
    assert not policy.permits("anyone@sub.team.example")  # a listed domain, not its subdomains
    assert not policy.permits("x@resend.dev.evil.example")
    assert RecipientPolicy().permits("anyone@example.org")  # live and mock


def test_each_mode_gets_its_sender_and_recipients() -> None:
    assert _sender(make_settings(email_mode="disabled")) is None
    assert isinstance(_sender(make_settings()), MockEmailSender)  # mock, the default
    sandbox = make_settings(
        email_mode="sandbox", resend_api_key=KEY, email_from=FROM, email_sandbox_recipients="a@b.cd"
    )
    sender = _sender(sandbox)
    assert isinstance(sender, ResendEmailSender)
    sender.close()
    assert _policy(sandbox).allowed == frozenset({"@resend.dev", "a@b.cd"})
    assert _policy(make_settings()).allowed is None


def test_the_description_names_the_sender_domain_and_never_the_listed_recipients() -> None:
    sandbox = make_settings(
        email_mode="sandbox",
        resend_api_key=KEY,
        email_from=FROM,
        email_sandbox_recipients="me@example.org,@team.example",
    )
    # The exact lines: no listed address or domain, and no key, can appear in them.
    assert describe(sandbox).details == (
        "Sender domain: example.com",
        "Recipients: Resend's test addresses and 2 EMAIL_SANDBOX_RECIPIENTS entries",
    )


# --- settings ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"email_mode": "live"}, "EMAIL_MODE=live is only allowed when APP_ENV=production"),
        ({"email_mode": "sandbox", "email_from": FROM}, "RESEND_API_KEY is required"),
        ({"email_mode": "sandbox", "resend_api_key": KEY}, "EMAIL_FROM is required"),
        ({"resend_api_key": "sk_test_" + "x" * 24}, "RESEND_API_KEY must be a Resend API key"),
        ({"email_sandbox_recipients": "me@example.org, not an address"}, "EMAIL_SANDBOX_RECIPIENTS must list"),
        (
            {"email_mode": "live", "resend_api_key": KEY, "email_from": "Example <onboarding@Resend.dev>"},
            "EMAIL_FROM must be on a domain of yours when EMAIL_MODE=live",
        ),
        (
            {"email_mode": "live", "resend_api_key": KEY, "email_from": "onboarding@resend.dev"},
            "EMAIL_FROM must be on a domain of yours when EMAIL_MODE=live",
        ),
    ],
)
def test_the_settings_refuse_an_unusable_email_configuration(settings: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        make_settings(**settings)
    assert message in str(excinfo.value)
    assert KEY not in str(excinfo.value)


def test_mock_and_disabled_need_no_key_and_sandbox_takes_one() -> None:
    assert make_settings(email_mode="disabled").email_mode.value == "disabled"
    assert make_settings().email_mode.value == "mock"
    configured = make_settings(
        email_mode="sandbox", resend_api_key=KEY, email_from=FROM, email_sandbox_recipients="@x.io"
    )
    assert configured.email_mode.value == "sandbox"
    # Resend's testing domain is a sandbox sender (it reaches the account's own address); the check names it.
    testing = make_settings(email_mode="sandbox", resend_api_key=KEY, email_from="onboarding@resend.dev")
    assert testing.email_from == "onboarding@resend.dev"


# --- the service ------------------------------------------------------------------------------------------


class RecordingSender:
    name = "recording"

    def __init__(self) -> None:
        self.sent: list[tuple[str, EmailMessage, str]] = []
        self.release = threading.Event()
        self.release.set()

    def send(self, sender: str, message: EmailMessage, *, idempotency_key: str) -> str:
        self.release.wait(5)
        self.sent.append((sender, message, idempotency_key))
        return f"id-{len(self.sent)}"

    def domains(self) -> DomainReport:
        return DomainReport()


def service(sender: Any, policy: RecipientPolicy | None = None, bound: int = 2) -> EmailService:
    return EmailService(
        sender,
        from_address=FROM,
        sender_address="noreply@example.com",
        policy=policy or RecipientPolicy(),
        max_concurrent_sends=bound,
    )


def test_a_send_goes_from_email_from_with_its_key() -> None:
    sender = RecordingSender()
    assert service(sender).send(MESSAGE, idempotency_key="projects.created:1") == "id-1"
    assert sender.sent == [(FROM, MESSAGE, "projects.created:1")]
    assert service(sender).sender_domain == "example.com"


def test_disabled_email_refuses_every_send_and_calls_nothing() -> None:
    email = service(None)
    assert not email.configured
    with pytest.raises(NotConfiguredError, match="EMAIL_MODE=disabled"):
        email.send(MESSAGE, idempotency_key="k")
    with pytest.raises(NotConfiguredError):
        email.domains()


def test_a_bad_message_is_refused_without_naming_its_addresses() -> None:
    sender = RecordingSender()
    with pytest.raises(InvalidRequestError) as excinfo:
        service(sender).send(_replace(MESSAGE, to=("ada@example.org", "broken")), idempotency_key="k")
    assert "broken" not in excinfo.value.public_message
    assert "ada@" not in excinfo.value.public_message
    assert sender.sent == []


def test_the_sandbox_refuses_a_real_recipient_before_resend_sees_it() -> None:
    sender = RecordingSender()
    email = service(sender, RecipientPolicy.sandbox(["me@example.org"]))
    with pytest.raises(InvalidRequestError) as excinfo:
        email.send(_replace(MESSAGE, to=("me@example.org", "ada@example.org")), idempotency_key="k")
    assert "1 of 2 recipients" in excinfo.value.public_message
    assert "ada@example.org" not in excinfo.value.public_message
    assert sender.sent == []
    email.send(_replace(MESSAGE, to=("me@example.org", TEST_RECIPIENT)), idempotency_key="k2")
    assert len(sender.sent) == 1


def test_sends_in_flight_are_bounded_per_instance() -> None:
    sender = RecordingSender()
    sender.release.clear()
    email = service(sender, bound=1)
    first = threading.Thread(target=lambda: email.send(MESSAGE, idempotency_key="a"))
    first.start()
    try:
        for _ in range(100):  # wait until the first send holds the only place
            if not email._in_flight.acquire(blocking=False):
                break
            email._in_flight.release()
            threading.Event().wait(0.01)
        with pytest.raises(RateLimitedError):
            email.send(MESSAGE, idempotency_key="b")
    finally:
        sender.release.set()
        first.join(5)
    assert [key for _, _, key in sender.sent] == ["a"]
    assert email.send(MESSAGE, idempotency_key="c") == "id-2"  # the place was given back


def test_the_mock_sender_keeps_one_email_per_key_and_logs_no_address(caplog: pytest.LogCaptureFixture) -> None:
    mock = MockEmailSender()
    with caplog.at_level(logging.INFO):
        first = mock.send(FROM, MESSAGE, idempotency_key="k")
        again = mock.send(FROM, MESSAGE, idempotency_key="k")
    assert first == again == "mock-email-1"
    assert len(mock.sent) == 1
    assert "ada@example.org" not in caplog.text
    assert "Welcome" not in caplog.text


# --- the Resend adapter, against a mock transport -----------------------------------------------------------


Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def resend(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[Handler], tuple[ResendEmailSender, list[float]]]]:
    """A ResendEmailSender whose client answers through ``handler``; the second item lists the waits it made."""
    real_client = httpx.Client
    made: list[ResendEmailSender] = []

    def build(handler: Handler, timeout: float = 10.0) -> tuple[ResendEmailSender, list[float]]:
        monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(**kw, transport=httpx.MockTransport(handler)))
        waits: list[float] = []
        sender = ResendEmailSender(api_key=KEY, timeout_seconds=timeout, sleep=waits.append)
        made.append(sender)
        return sender, waits

    yield build
    for sender in made:
        sender.close()


def error(status: int, name: str, **headers: str) -> httpx.Response:
    return httpx.Response(
        status, json={"statusCode": status, "name": name, "message": "ada@example.org said no"}, headers=headers
    )


def test_a_send_is_the_documented_request(resend: Any) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794"})

    sender, _ = resend(handler)
    message = _replace(MESSAGE, html="<p>Hello.</p>", reply_to="help@example.com", tags=(("source", "signup"),))
    assert sender.send(FROM, message, idempotency_key="welcome:42") == "49a3999c-0ce1-4ea6-ab68-afcd6dc2e794"
    [request] = seen
    assert (request.method, str(request.url)) == ("POST", "https://api.resend.com/emails")
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["idempotency-key"] == "welcome:42"
    assert request.headers["user-agent"] == USER_AGENT  # Resend refuses a request without one (403)
    assert json.loads(request.content) == {
        "from": FROM,
        "to": ["ada@example.org"],
        "subject": "Welcome",
        "text": "Hello.",
        "html": "<p>Hello.</p>",
        "reply_to": "help@example.com",
        "tags": [{"name": "source", "value": "signup"}],
    }


@pytest.mark.parametrize(
    ("first", "waited"),
    [
        (error(429, "rate_limit_exceeded", **{"retry-after": "1"}), [1.0]),
        (error(409, "concurrent_idempotent_requests"), [0.5]),
        (error(500, "application_error"), [0.5]),
        (error(503, "service_unavailable"), [0.5]),
    ],
)
def test_a_send_resend_did_not_complete_is_repeated_under_the_same_key(
    resend: Any, first: httpx.Response, waited: list[float]
) -> None:
    keys: list[str] = []
    answers = iter([first, httpx.Response(200, json={"id": "e1"})])

    def handler(request: httpx.Request) -> httpx.Response:
        keys.append(request.headers["idempotency-key"])
        return next(answers)

    sender, waits = resend(handler)
    assert sender.send(FROM, MESSAGE, idempotency_key="k") == "e1"
    assert keys == ["k", "k"]
    assert waits == waited


def test_a_lost_connection_or_a_read_timeout_is_repeated_because_the_key_makes_it_safe(resend: Any) -> None:
    failures: list[Exception] = [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")]

    def handler(request: httpx.Request) -> httpx.Response:
        if failures:
            raise failures.pop(0)
        return httpx.Response(200, json={"id": "e2"})

    sender, waits = resend(handler)
    assert sender.send(FROM, MESSAGE, idempotency_key="k") == "e2"
    assert waits == [0.5, 1.0]


@pytest.mark.parametrize(
    ("answer", "kind", "words"),
    [
        (error(401, "validation_error"), ProviderRejectedError, "RESEND_API_KEY"),
        (error(403, "validation_error"), ProviderRejectedError, "verified"),  # the domain is not verified
        (error(422, "validation_error"), InvalidRequestError, "validation_error"),
        (error(409, "invalid_idempotent_request"), InvalidRequestError, "already used"),
        (error(429, "daily_quota_exceeded"), DependencyUnavailableError, "quota"),
        (error(429, "monthly_quota_exceeded"), DependencyUnavailableError, "quota"),
        (error(429, "rate_limit_exceeded", **{"retry-after": "60"}), DependencyUnavailableError, "rate limiting"),
        (httpx.Response(200, json={}), DependencyUnavailableError, "unavailable"),
        (httpx.Response(200, text="<html>"), DependencyUnavailableError, "unavailable"),
    ],
)
def test_a_refusal_is_never_repeated_and_never_quotes_resend(
    resend: Any, caplog: pytest.LogCaptureFixture, answer: httpx.Response, kind: type[Exception], words: str
) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return answer

    sender, waits = resend(handler)
    with caplog.at_level(logging.WARNING), pytest.raises(kind) as excinfo:
        sender.send(FROM, MESSAGE, idempotency_key="k")
    assert calls == [1]
    assert waits == []
    message = str(getattr(excinfo.value, "public_message", excinfo.value))
    assert words in message
    for text in (message, caplog.text):
        assert "ada@example.org" not in text
        assert KEY not in text
        assert "Welcome" not in text


def test_retries_stop_at_two_and_within_the_timeout(resend: Any) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return error(500, "application_error")

    sender, waits = resend(handler)
    with pytest.raises(DependencyUnavailableError):
        sender.send(FROM, MESSAGE, idempotency_key="k")
    assert len(calls) == 3
    assert waits == [0.5, 1.0]

    calls.clear()
    short, short_waits = resend(handler, timeout=0.2)
    with pytest.raises(DependencyUnavailableError):
        short.send(FROM, MESSAGE, idempotency_key="k")
    assert calls == [1]
    assert short_waits == []  # a wait that does not fit in the timeout is not made


def test_the_domains_are_read_as_resend_lists_them(resend: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path, request.url.query) == ("GET", "/domains", b"")
        return httpx.Response(
            200,
            json={
                "object": "list",
                "has_more": False,
                "data": [
                    {"id": "d1", "name": "example.com", "status": "verified", "capabilities": {"sending": "enabled"}},
                    {"id": "d2", "name": "mail.example.org", "status": "pending"},
                    {"id": "d3", "name": "off.example", "status": "verified", "capabilities": {"sending": "disabled"}},
                    {"id": "d4"},
                    {"id": "d5", "name": "odd.example", "status": "<b>Verified</b>"},
                    {"id": "d6", "name": "none.example", "status": None},
                ],
            },
        )

    sender, _ = resend(handler)
    report = sender.domains()
    assert report == DomainReport(
        (
            SendingDomain("example.com", "verified", True),
            SendingDomain("mail.example.org", "pending", None),
            SendingDomain("off.example", "verified", False),
            SendingDomain("odd.example", "unknown", None),  # a status the check would quote is bounded
            SendingDomain("none.example", "unknown", None),
        )
    )
    assert report.find("EXAMPLE.com") == SendingDomain("example.com", "verified", True)


@pytest.mark.parametrize("body", [{"name": "restricted_api_key"}, {}])
def test_a_key_that_may_only_send_is_valid_but_cannot_list_domains(resend: Any, body: dict[str, str]) -> None:
    sender, waits = resend(lambda request: httpx.Response(401, json=body))
    assert sender.domains() == DomainReport(sending_only_key=True)
    assert waits == []


def test_an_unknown_key_on_the_domains_list_is_a_rejected_key(resend: Any) -> None:
    """Every endpoint but sending answers an unknown key with 400 validation_error."""
    sender, _ = resend(lambda request: error(400, "validation_error"))
    with pytest.raises(ProviderRejectedError, match="RESEND_API_KEY"):
        sender.domains()


def test_the_client_follows_no_redirect_and_speaks_only_https(resend: Any) -> None:
    sender, _ = resend(lambda request: httpx.Response(302, headers={"location": "http://elsewhere.example/"}))
    with pytest.raises(DependencyUnavailableError):
        sender.send(FROM, MESSAGE, idempotency_key="k")
    assert adapters.RESEND_API.startswith("https://")
