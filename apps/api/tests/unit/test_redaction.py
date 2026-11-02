"""Redaction of logs and telemetry: what each pattern removes, and what it costs (OBS-02, OBS-04, OBS-06)."""

from __future__ import annotations

import json
import logging
import random
import re
import time
from typing import Any

import pytest

from app.kernel.infrastructure.observability import JsonFormatter, scrub_breadcrumb, scrub_event
from app.kernel.infrastructure.redaction import MAX_TEXT_LENGTH, REDACTED, TRUNCATED, redact, redact_text
from app.kernel.request_context import request_id_var

JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJjYW5hcnkifQ.c2lnbmF0dXJlY2FuYXJ5"
UNICODE_ADDRESSES = [
    pytest.param("josé@example.com", id="an accented letter"),
    pytest.param("jose\u0301@example.com", id="a letter and a combining accent"),
    pytest.param("josé+tag@example.co.uk", id="a plus tag after an accent"),
    pytest.param("иван@example.ru", id="Cyrillic"),
    pytest.param("用户@example.cn", id="Chinese"),
    pytest.param("user@münchen.de", id="an internationalized domain"),
    pytest.param("иван@пример.рф", id="a domain and a top-level domain in Cyrillic"),
    pytest.param("用户@例子.中国", id="a domain and a top-level domain in Chinese"),
]
SECRET = "sk_" + "live_CANARY1234567890"
MB = 1_000_000


def elapsed(run: Any) -> tuple[Any, float]:
    """``run()`` and the CPU time it took, which a busy machine does not inflate as it does wall time."""
    started = time.thread_time()
    result = run()
    return result, time.thread_time() - started


@pytest.fixture
def capped() -> None:
    """Fails fast without the cap, which would leave a quadratic pattern minutes of work on a megabyte."""
    assert redact_text("x " * MAX_TEXT_LENGTH).endswith(TRUNCATED)


class TestPatterns:
    @pytest.mark.parametrize("length", [100, 300])
    def test_an_email_address_is_redacted_whole_however_long_its_local_part(self, length: int) -> None:
        local = "jane.doe" + "a" * (length - len("jane.doe"))
        assert redact_text(f"x {local}@example.com y") == f"x {REDACTED} y"

    @pytest.mark.parametrize("joiner", ["+", "-", "_", "%", "7"])
    def test_an_email_address_written_right_after_another_is_redacted(self, joiner: str) -> None:
        assert not re.search("jane|bob|example", redact_text(f"x to=jane@example.com{joiner}bob@example.org y"))

    @pytest.mark.parametrize("address", UNICODE_ADDRESSES)
    def test_an_email_address_in_any_script_is_redacted(self, address: str) -> None:
        assert redact_text(f"failed for {address} now") == f"failed for {REDACTED} now"

    @pytest.mark.parametrize("joiner", ["+", "-", "_", "%", "7"])
    def test_an_email_address_in_any_script_written_right_after_another_is_redacted(self, joiner: str) -> None:
        assert redact_text(f"x to=josé@example.com{joiner}иван@пример.рф y") == f"x to={REDACTED} y"

    @pytest.mark.parametrize("text", ["ça va@bien", "a@b", "5€ @ 3", "@пример.рф", "иван@", "иван@пример"])
    def test_text_without_the_parts_of_an_address_is_kept(self, text: str) -> None:
        assert redact_text(f"x {text} y") == f"x {text} y"

    @pytest.mark.parametrize(
        ("prefix", "kept"),
        [("id-", "id-"), ("ref.abc.", "ref.abc."), ("abc-eyJxxxx-", "abc-")],
        ids=["after a hyphen", "after a dot", "from the first eyJ of a longer run"],
    )
    def test_a_jwt_is_redacted_wherever_it_starts(self, prefix: str, kept: str) -> None:
        assert redact_text(f"{prefix}{JWT} end") == f"{kept}{REDACTED} end"

    @pytest.mark.parametrize(
        ("text", "redacted"),
        [
            ("postgresql://app:hunter2@db:5432/app", f"postgresql://app:{REDACTED}@db:5432/app"),
            ("postgres://app:pa/ss@db", f"postgres://app:{REDACTED}@db"),
            ("POSTGRES://app:a://b@db", f"POSTGRES://app:{REDACTED}@db"),
            ("a=postgres://u:p1@h b=postgresql://v:p2@h", f"a=postgres://u:{REDACTED}@h b=postgresql://v:{REDACTED}@h"),
            ("postgres://db.example:5432/app", "postgres://db.example:5432/app"),
            ("postgres://u:not-a-dsn postgres://v:p@h", f"postgres://u:not-a-dsn postgres://v:{REDACTED}@h"),
            ("postgres://u:@h", "postgres://u:@h"),
            pytest.param(
                "postgres://jane@example.com:pw@db",
                f"postgres://{REDACTED}:{REDACTED}@db",
                id="after an email address as the user",
            ),
            # The end of a password and a dotted host read as an email address, so the host goes too.
            pytest.param(
                "postgresql://app:hunter2@db.example:5432/app",
                f"postgresql://app:{REDACTED}:5432/app",
                id="before a dotted host",
            ),
            pytest.param(
                "postgresql://app:Abc$123xyz@prod-db.abc123.us-east-1.rds.amazonaws.com:5432/app",
                f"postgresql://app:{REDACTED}:5432/app",
                id="with a special character, before a dotted host",
            ),
            pytest.param(
                "postgresql://postgres.abcdefghij:S3cr3t!Pass@aws-0-us-east-1.pooler.supabase.com:6543/postgres",
                f"postgresql://postgres.abcdefghij:{REDACTED}:6543/postgres",
                id="of a Supabase pooler",
            ),
            pytest.param(
                "postgresql://app:pass!@db.example.com:5432/app",
                f"postgresql://app:{REDACTED}@db.example.com:5432/app",
                id="ending in a character no address holds, before a dotted host",
            ),
            # Another "@" in the word may belong to something after the DSN, so the rest of the word goes too.
            pytest.param(
                "input_value='postgresql://app:P@ssw0rd!@db.example.com:5432/app', input_type=str",
                f"input_value='postgresql://app:{REDACTED} input_type=str",
                id="with a raw @",
            ),
            pytest.param("postgres://app:aB3@x/9!@db", f"postgres://app:{REDACTED}", id="with a raw @ and a slash"),
            pytest.param(
                "postgres://u:pw@db/app?application_name=a@b",
                f"postgres://u:{REDACTED}",
                id="before a query holding an @",
            ),
            pytest.param(
                '{"database_url":"postgres://u:pw@db/app","owner":"jane.doe@janedoe.name"}',
                f'{{"database_url":"postgres://u:{REDACTED}',
                id="before an address in compact JSON",
            ),
            pytest.param(
                "postgresql://app:pw@db:5432/app?sslmode=require&admin=jane@acme-corp.com",
                f"postgresql://app:{REDACTED}",
                id="before an address in its query",
            ),
            # A DSN without a password takes the "@" of what follows it, which then goes whole.
            pytest.param(
                '{"db":"postgres://db.internal:5432/app","owner":"jane.doe@acme-corp.com"}',
                f'{{"db":"postgres://db.internal:{REDACTED}"}}',
                id="without a password, before an address in compact JSON",
            ),
            pytest.param(
                "DATABASE_URL=postgres://localhost:5432/app,ADMIN=jane@acme-corp.com",
                f"DATABASE_URL=postgres://localhost:{REDACTED}",
                id="without a password, before an address",
            ),
            pytest.param(
                "postgres://db.internal:5432/app?token=ab@cd&x=1",
                f"postgres://db.internal:{REDACTED}&x=1",
                id="without a password, before a token holding an @",
            ),
        ],
    )
    def test_dsn_passwords(self, text: str, redacted: str) -> None:
        assert redact_text(text) == redacted

    @pytest.mark.parametrize(
        "name",
        [
            *("token", "access_token", "id_token", "code", "session_id", "key", "api_key", "secret", "signature"),
            # Clerk's, as apps/web/src/lib/redact.ts lists them, and one it may add.
            *("__clerk_ticket", "__clerk_invitation_token", "__clerk_status", "__clerk_handshake"),
            *("__clerk_handshake_nonce", "__clerk_db_jwt", "__clerk_created_session", "__clerk_testing_token"),
            *("__clerk_modal_state", "__clerk_redirect_url", "__clerk_satellite_url", "__clerk_future_param"),
            *("redirect_url", "ticket", "__CLERK_TICKET"),
            # deployment.md puts the Protection Bypass secret in the query of a preview's webhook URL.
            *("password", "x-vercel-protection-bypass", "X-Vercel-Protection-Bypass"),
        ],
    )
    def test_the_value_of_a_sensitive_query_parameter_is_redacted(self, name: str) -> None:
        assert redact_text(f"/p?a=1&{name}=CANARY&b=2") == f"/p?a=1&{name}={REDACTED}&b=2"

    def test_tokens_in_urls_are_redacted_from_error_events_and_breadcrumbs(self) -> None:
        url = "https://app.example.com/sign-in?__clerk_ticket=TICKET1&ticket=TICKET2&redirect_url=https://a/b?c=TICKET3"
        event = {
            "exception": {"values": [{"type": "HTTPStatusError", "value": f"Client error for url '{url}'"}]},
            "message": "see https://app.example.com/?__clerk_ticket=TICKET4",
            "logentry": {
                "message": "visit %s",
                "formatted": "visit https://app.example.com/?__clerk_handshake=TICKET5",
            },
        }
        crumb = {"message": "GET https://app.example.com/?__clerk_ticket=TICKET6", "data": {"to": url}}
        assert "TICKET" not in json.dumps([scrub_event(event, None), scrub_breadcrumb(crumb, None)])


def fuzz(fragments: list[str], *, seed: int, count: int = 20_000, pieces: int = 12) -> list[str]:
    rng = random.Random(seed)
    return ["".join(rng.choices(fragments, k=rng.randint(1, pieces))) for _ in range(count)]


DSN_FRAGMENTS = ["postgres://", "postgresql://", "PostgreSQL://", "u", ":", "p", "@", "h", " ", "/", "x", "\n"]

# The redaction before linear-time patterns (37a4519), whole and in its order.
PREVIOUS_PATTERNS = (
    (re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{6,}"), REDACTED),
    (re.compile(r"\bwhsec_[A-Za-z0-9+/=]{6,}"), REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{10,}"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"), REDACTED),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), REDACTED),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), REDACTED),
    (re.compile(r"(?i)(postgres(?:ql)?://[^:/\s]+:)[^@\s]+(@)"), rf"\g<1>{REDACTED}\g<2>"),
    (
        re.compile(
            r"(?i)([?&](?:token|access_token|id_token|code|session_id|key|api_key|secret|signature)=)[^&\s\"']+"
        ),
        rf"\g<1>{REDACTED}",
    ),
)


def previous_redaction(text: str) -> str:
    for pattern, replacement in PREVIOUS_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# Secrets ({i} is a digit), each with the parts of it that must not show.
SECRETS = [
    ("jdoe{i}x@corp{i}z.com", ["jdoe{i}x", "corp{i}z"]),
    ("a.b+{i}k@mail{i}q.example.org", ["{i}k@", "mail{i}q"]),
    ("eyJhbG{i}cA.eyJzdW{i}cB.c2lnb{i}cC", ["eyJhbG{i}cA", "eyJzdW{i}cB", "c2lnb{i}cC"]),
    ("sk_live_K{i}KEYabc", ["K{i}KEYabc"]),
    ("Bearer B{i}BEARERxyz", ["B{i}BEARERxyz"]),
    ("postgres://app:P{i}Pw!x@db{i}.example.com:5432/app", ["P{i}Pw!x"]),
    ("postgresql://app:Pw{i}rd9@db{i}:5432/app", ["Pw{i}rd9"]),
    ("postgresql://app:Q{i}@Qw#y@db.example.com/app", ["Q{i}@Qw#y"]),
    ("postgres://u:S{i}ecret@h/db?sslmode=require", ["S{i}ecret"]),
    ("postgres://db{i}.internal:5432/app", []),
    ("postgresql://localhost:5432/app{i}", []),
    ("/p?a=1&token=T{i}TOKx&b=2", ["T{i}TOKx"]),
    ("?token=V{i}al@Ue{i}x", ["V{i}al", "Ue{i}x"]),
]
# What joins them: nothing, one character, or compact JSON.
JOINERS = ["", *" ,;&\"'=:/?\n@-.+x_", '","', '","owner":"']


class TestSameRedactionAsThePreviousPatterns:
    """The linear patterns redact what the previous, quadratic ones did (ASCII text)."""

    def test_email_addresses(self) -> None:
        previous = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
        fragments = ["ab", "c", ".", "@", "+", "-", "1", " ", "x.yz", "@q.rs", ".tu", "_", "%", ".v", ",", "@a", "b.cd"]
        for text in fuzz(fragments, seed=1):
            # Addresses written one after another, which matched one by one, are now one match.
            expected = re.sub(r"(?:\[REDACTED\])+", REDACTED, previous.sub(REDACTED, text))
            assert redact_text(text) == expected, text

    def test_jwts(self) -> None:
        previous = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
        fragments = ["eyJ", "a", "-", ".", "abcde", "_", " ", "x", "eyJab", "Z9", "..", "eyJabcde"]
        for text in fuzz(fragments, seed=2, pieces=14):
            assert redact_text(text) == previous.sub(REDACTED, text), text

    def test_dsn_passwords(self) -> None:
        """The previous pattern, which ended a password at its first "@", finds nothing left to redact."""
        previous = re.compile(r"(?i)(postgres(?:ql)?://[^:/\s]+:)[^@\s]+(@)")
        for text in fuzz(DSN_FRAGMENTS, seed=3):
            redacted = redact_text(text)
            assert previous.sub(rf"\g<1>{REDACTED}\g<2>", redacted) == redacted, text

    def test_a_dsn_password_runs_to_the_last_at_of_its_word(self) -> None:
        """And with another "@" before that one, the rest of the word goes too (these fragments hold no address)."""
        plain = re.compile(r"(?i)(postgres(?:ql)?://[^:/\s]+:)(?:\S*@\S*@\S*|[^@\s]+(?=@))")  # quadratic on long text
        for text in fuzz(DSN_FRAGMENTS, seed=4):
            assert redact_text(text) == plain.sub(rf"\g<1>{REDACTED}", text), text

    def test_nothing_the_previous_redaction_hid_shows(self) -> None:
        """Secrets written into one word with DSNs, with or without a password, as compact JSON or a query can."""
        rng = random.Random(5)
        for _ in range(20_000):
            parts, canaries = [], []
            for _ in range(rng.randint(1, 4)):
                secret, hidden = rng.choice(SECRETS)
                i = rng.randint(0, 9)
                parts += [secret.format(i=i), rng.choice(JOINERS) + rng.choice(JOINERS)]
                canaries += [canary.format(i=i) for canary in hidden]
            text = "".join(parts)
            before, after = previous_redaction(text), redact_text(text)
            assert not [canary for canary in canaries if canary not in before and canary in after], text


class TestRedactionCost:
    """OBS-06: every pattern runs in linear time, and each string is capped, so the work is bounded."""

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("a" * 40_000, id="a run of local-part characters"),
            pytest.param("a.%+-_" * 7_000, id="mixed local-part characters"),
            pytest.param("a" * 40_000 + "@", id="local-part characters before an @"),
            pytest.param("a@" * 20_000, id="many @"),
            pytest.param("a@" + "b." * 20_000, id="dotted labels after an @"),
            pytest.param("a" * 20_000 + "@" + "b." * 10_000, id="a long local part before a dotted domain"),
            pytest.param("a@b.cc" + ".d@e" * 10_000, id="near-addresses after an address"),
            pytest.param("é" * 40_000, id="a run of accented letters"),
            pytest.param("é.%+-_" * 7_000, id="mixed local-part characters, accented"),
            pytest.param("é" * 40_000 + "@", id="accented local-part characters before an @"),
            pytest.param("é" * 20_000 + "@" + "д." * 10_000, id="an accented local part before a dotted domain"),
            pytest.param("é@b.cc" + ".д@e" * 10_000, id="near-addresses after an accented address"),
            pytest.param("eyJ-" * 10_000, id="a run of JWT-like segments"),
            pytest.param("eyJaaaaa." * 5_000, id="dotted JWT-like segments"),
            pytest.param("postgres://" * 4_000, id="DSN schemes"),
            pytest.param("postgres://u:" * 3_000, id="DSN user names"),
            pytest.param("postgres://u:" + "p" * 40_000, id="a DSN password without an @"),
            pytest.param("postgres://u:p" + "@a" * 20_000, id="a DSN password with many @"),
            pytest.param("postgres://u:p@" + "a/" * 20_000, id="a long path after a DSN"),
            pytest.param(
                "postgres://u:" + "a" * 20_000 + "@" + "b." * 10_000, id="a DSN password before a near-domain"
            ),
            pytest.param("postgres://h:5432/?token=" + "v" * 40_000 + "@x", id="a long query value across a DSN's @"),
            pytest.param("sk-" * 13_000, id="API key prefixes"),
            pytest.param("bearer " * 6_000, id="bearer prefixes"),
            pytest.param("?token=" * 6_000, id="token parameters"),
            pytest.param("?__clerk" + "_" * 40_000, id="a Clerk parameter name without its ="),
            pytest.param("&__clerk_" * 5_000, id="Clerk parameter names"),
        ],
    )
    def test_linear_time_without_the_cap(self, text: str) -> None:
        _, seconds = elapsed(lambda: redact_text(text, max_length=len(text)))
        assert seconds < 0.1

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("a" * MB, id="local-part characters"),
            pytest.param("eyJ-" * (MB // 4), id="JWT-like segments"),
            pytest.param("postgres://" * (MB // 11), id="DSN schemes"),
            pytest.param(f"{SECRET} " + "a" * MB, id="a key and a long word"),
        ],
    )
    @pytest.mark.usefixtures("capped")
    def test_a_megabyte_is_redacted_in_well_under_100_ms(self, text: str) -> None:
        redacted, seconds = elapsed(lambda: redact_text(text))
        assert seconds < 0.1
        assert redacted.endswith(TRUNCATED)
        assert SECRET not in redacted

    def test_long_text_keeps_its_first_4096_characters_without_a_token_cut_in_half(self) -> None:
        filler = "word " * 819  # 4095 characters
        assert redact_text(f"{filler}x{SECRET} {SECRET}") == f"{filler}{TRUNCATED}"
        assert redact_text(f"{filler}x") == f"{filler}x"

    @pytest.mark.parametrize(
        ("secret", "kept"),
        [(JWT, ""), (SECRET, ""), ("canary.person@example.com", ""), ("Bearer abcdefghijklmnopqrstuvwxyz", "Bearer ")],
    )
    def test_a_secret_cut_by_the_cap_is_dropped(self, secret: str, kept: str) -> None:
        filler = "x " * 2_044  # 4088 characters: the cap falls inside the secret
        assert redact_text(f"{filler}{secret} tail") == f"{filler}{kept}{TRUNCATED}"

    @pytest.mark.parametrize(
        "address", ["josé@example.com", "jose\u0301@example.com", "иван@пример.рф", "用户@例子.中国"]
    )
    def test_an_address_in_any_script_is_dropped_wherever_the_cap_cuts_it(self, address: str) -> None:
        kept = set()
        for cut in range(len(address) + 1):  # the cap falls `cut` characters into the address
            filler = " " * (MAX_TEXT_LENGTH - cut)
            kept.add(redact_text(f"{filler}{address} tail").removeprefix(filler).removesuffix(TRUNCATED))
        assert kept == {""}

    @pytest.mark.parametrize("password", ["Tr0ub4dor&3xyz", "p:ss:word9", "S3cr3t!Pass", "a$b#c*d(e)f[g]h"])
    def test_a_dsn_cut_by_the_cap_is_dropped_wherever_the_cut_falls(self, password: str) -> None:
        """A DSN's password can hold any character but whitespace, so the cut can leave its start without its "@"."""
        dsn = f"postgres://app:{password}@db.example.com:5432/app"
        kept = set()
        for cut in range(len(dsn) + 1):  # the cap falls `cut` characters into the DSN
            filler = " " * (MAX_TEXT_LENGTH - cut)
            kept.add(redact_text(f"{filler}{dsn} tail").removeprefix(filler).removesuffix(TRUNCATED))
        assert not [text for text in kept if f"app:{password[0]}" in text]
        # Only a scheme cut before its "//" is left, and it holds nothing.
        assert kept <= {"", "postgres:"}

    def test_a_word_holding_a_dsn_cut_by_the_cap_is_dropped_whole(self) -> None:
        text = "x" * (MAX_TEXT_LENGTH - 27) + "postgres://app:Tr0ub4dor&3xyz@localhost:5432/app tail"
        assert redact_text(text) == TRUNCATED
        assert redact_text(f"keep {text[5:]}") == f"keep {TRUNCATED}"

    @pytest.mark.usefixtures("capped")
    def test_nested_values_are_capped_one_by_one(self) -> None:
        redacted, seconds = elapsed(lambda: redact({"blob": f"{SECRET} " + "a" * MB, "list": ["b" * MB] * 3}))
        assert seconds < 0.1
        assert redacted["blob"] == f"{REDACTED} {TRUNCATED}"
        assert redacted["list"] == [TRUNCATED] * 3

    @pytest.mark.usefixtures("capped")
    def test_a_megabyte_log_message_is_capped(self) -> None:
        record = logging.LogRecord("app", logging.ERROR, __file__, 1, "failed: %s", (f"{SECRET} " + "a" * MB,), None)
        line, seconds = elapsed(lambda: json.loads(JsonFormatter().format(record)))
        assert seconds < 0.1
        assert line["msg"] == f"failed: {REDACTED} {TRUNCATED}"

    @pytest.mark.usefixtures("capped")
    def test_a_sentry_event_with_megabyte_values_is_scrubbed_in_well_under_100_ms(self) -> None:
        event = {
            "message": "eyJ-" * (MB // 4),
            "exception": {"values": [{"value": f"{SECRET} " + "a" * MB}]},
            "extra": {"blob": "postgres://" * (MB // 11)},
            "logentry": {"message": "%s", "formatted": "a@" * (MB // 2)},
        }
        scrubbed, seconds = elapsed(lambda: scrub_event(event, None))
        assert seconds < 0.1
        assert scrubbed["exception"]["values"][0]["value"] == f"{REDACTED} {TRUNCATED}"
        assert all(len(text) <= 4096 + len(TRUNCATED) for text in (scrubbed["message"], scrubbed["extra"]["blob"]))


class TestLogLinesRedactEveryValue:
    """OBS-04: ``redact`` walks strings, mappings and lists, so a value of another type (an exception, a set, bytes)
    reached the line through ``json.dumps(default=str)`` unredacted."""

    @staticmethod
    def line(**extra: Any) -> str:
        record = logging.LogRecord("app", logging.WARNING, __file__, 1, "something_failed", None, None)
        record.__dict__.update(extra)
        return JsonFormatter().format(record)

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(ValueError(f"declined with {SECRET}"), id="an exception"),
            pytest.param({SECRET}, id="a set"),
            pytest.param(SECRET.encode(), id="bytes"),
            pytest.param((SECRET,), id="a tuple"),  # control: already walked
            pytest.param(f"declined with {SECRET}", id="a string"),  # control: already redacted
        ],
    )
    def test_no_value_type_prints_a_secret(self, value: object) -> None:
        text = self.line(error=value)
        assert SECRET not in text
        assert "CANARY1234567890" not in text

    def test_a_value_without_a_secret_still_prints(self) -> None:
        """The control: redaction changes what is secret, not what is printed."""
        line = json.loads(self.line(error=ValueError("no such project")))
        assert line["error"] == "no such project"


class TestRequestIdInLogLines:
    """OBS-05: ``request_id`` on a log line is the API request's ID, which an operator filters by."""

    REQUEST = "0123456789abcdef0123456789abcdef"

    @staticmethod
    def line(**extra: Any) -> dict[str, Any]:
        record = logging.LogRecord("app", logging.WARNING, __file__, 1, "something_failed", None, None)
        record.__dict__.update(extra)
        line: dict[str, Any] = json.loads(JsonFormatter().format(record))
        return line

    def test_a_log_call_cannot_replace_it(self) -> None:
        """A provider's request ID, passed as ``extra={"request_id": ...}``, is kept under another name."""
        token = request_id_var.set(self.REQUEST)
        try:
            line = self.line(request_id="req_CANARYPROVIDER1")
        finally:
            request_id_var.reset(token)
        assert line["request_id"] == self.REQUEST
        assert line["extra_request_id"] == "req_CANARYPROVIDER1"

    def test_outside_a_request_the_line_has_none_of_its_own(self) -> None:
        line = self.line(request_id="req_CANARYPROVIDER1")
        assert "request_id" not in line
        assert line["extra_request_id"] == "req_CANARYPROVIDER1"
