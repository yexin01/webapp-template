"""``.env.example`` names the variables the API reads, tagged for the app that reads them (ENV-06).

Four lists are kept by hand: the API's settings (``Settings`` and ``DatabaseSettings``), the web app's
variables, the bundle scan's ``SERVER_ONLY_NAMES``, and the file an adopter edits. This is the API half of
the check that they agree; the web half is apps/web/src/lib/env-example-parity.test.ts. Both read the file
by the rules its legend gives: a variable is a line ``NAME=`` or ``# NAME=``; its tag is the ``# >`` line in
the comment block just above it, else the default in the title of its section (the Advanced section).

``problems`` is a pure function of the file's text, so the last tests show what each kind of drift reports.
A failure names the variable and what to change.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import get_args

import pytest
from pydantic import AliasChoices, BaseModel, SecretStr

from app.kernel.infrastructure.config import REPO_ROOT, DatabaseSettings
from app.settings import Settings

assert REPO_ROOT is not None
BUNDLE_SCAN = REPO_ROOT / "scripts" / "check-bundle-secrets.mjs"

# The legend at the top of the file.
VOCABULARY = {
    "apps": {"web", "api", "both", "cli"},
    "kind": {"public", "secret", "config"},
    "need": {"always", "deployed", "mode", "optional"},
    "scopes": {"all", "preview+production", "production", "not-on-vercel", "build"},
}
PLATFORM_LEAD = "# Set by the platform"
# Names Vercel sets on a deployment that the API reads. Read, never written: the header lists them, the file has no line.
PLATFORM = {"VERCEL_ENV", "VERCEL_URL", "VERCEL_BRANCH_URL", "VERCEL_GIT_COMMIT_SHA"}
# Read by test tooling, not by the apps: no tag line. Each has the file that reads it, which must still name it, and why.
TOOLING = {
    "E2E_CLERK_USER_EMAIL": ("apps/web/e2e/support/sandbox.ts", "the test user of the sandbox end-to-end run"),
    "E2E_DATABASE_URL": ("apps/web/playwright.config.ts", "the database of the account-free end-to-end API server"),
}
# Settings the API keeps out of its logs (SecretStr) that are not secrets, so the file tags them kind=config.
NOT_SECRET = {
    "SENTRY_DSN": "a public client key: the browser receives the same value as NEXT_PUBLIC_SENTRY_DSN",
    "CLERK_JWT_KEY": "a PEM public key, which Clerk serves to anyone",
}
# Of those, the ones the bundle scan does not compare either.
NOT_SCANNED = {"SENTRY_DSN"}

TAG_LINE = re.compile(r"# > (.+)")
VARIABLE = re.compile(r"(?:# )?([A-Z][A-Z0-9_]*)=")
SECTION_DEFAULT = re.compile(r"\((apps=[^,)]*)")


def setting_names(model: type[BaseModel]) -> dict[str, list[str]]:
    """The environment names each field is read from, upper-case: its aliases if it has any, else its own name."""
    names: dict[str, list[str]] = {}
    for field, info in model.model_fields.items():
        alias = info.validation_alias
        choices = alias.choices if isinstance(alias, AliasChoices) else [alias]
        names[field] = [choice.upper() for choice in choices if isinstance(choice, str)] or [field.upper()]
    return names


def secret_fields(model: type[BaseModel]) -> set[str]:
    return {
        field
        for field, info in model.model_fields.items()
        if info.annotation is SecretStr or SecretStr in get_args(info.annotation)
    }


API = setting_names(Settings)
DATABASE = setting_names(DatabaseSettings)
API_NAMES = {name for names in API.values() for name in names}
DATABASE_NAMES = {name for names in DATABASE.values() for name in names}

Tag = dict[str, str | bool]


@dataclass(frozen=True)
class Entry:
    name: str
    line: int
    tag: Tag | None  # None: no tag line above it and no default in its section


def parse_tag(text: str) -> tuple[Tag, list[str]]:
    tag: Tag = {"baked": False}
    unknown = []
    for token in text.split():
        key, _, value = token.partition("=")
        if token == "baked":
            tag["baked"] = True
        elif value in VOCABULARY.get(key, ()):
            tag[key] = value
        else:
            unknown.append(token)
    return tag, unknown


def parse(text: str) -> tuple[dict[str, Entry], list[str]]:
    """The file's variables, and the tag problems found on the way."""
    lines = text.splitlines()
    entries: dict[str, Entry] = {}
    problems: list[str] = []
    default: Tag | None = None
    after_separator = False
    for number, line in enumerate(lines, start=1):
        is_title = after_separator and line.startswith("# ")
        after_separator = line.startswith("# =====")
        if is_title:  # a section's title may carry the tag of every variable in it
            found = SECTION_DEFAULT.search(line)
            default = parse_tag(found.group(1))[0] if found else None
        if (match := VARIABLE.match(line)) is None:
            continue
        name = match.group(1)
        tag = default
        for above in reversed(lines[: number - 1]):
            if not above.startswith("# ") or above.startswith("# =====") or VARIABLE.match(above):
                break
            if (tagged := TAG_LINE.fullmatch(above)) is not None:
                tag, unknown = parse_tag(tagged.group(1))
                problems += [f"{name}: the tag has {token!r}, which the legend does not define" for token in unknown]
                break
        if tag is not None and (missing := [key for key in VOCABULARY if key not in tag]):
            problems.append(f"{name}: the tag lacks {', '.join(f'{key}=' for key in missing)}")
        if name in entries:
            problems.append(f"{name}: listed twice (lines {entries[name].line} and {number}); keep one")
        entries.setdefault(name, Entry(name, number, tag))
    return entries, problems


def listed_under(text: str, lead: str) -> set[str]:
    """The names the header lists on the indented lines under the line that starts with ``lead``."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(lead)), None)
    names: set[str] = set()
    for line in lines[start + 1 :] if start is not None else []:
        if not line.startswith("#   "):
            break
        names.update(re.findall(r"[A-Z][A-Z0-9_]+", line))
    return names


def mentioned(text: str, name: str) -> bool:
    return re.search(rf"(?<![A-Z0-9_]){name}(?![A-Z0-9_])", text) is not None


def scanned_names() -> set[str]:
    """SERVER_ONLY_NAMES in scripts/check-bundle-secrets.mjs: the variables whose values the bundle scan compares."""
    listing = re.search(r"const SERVER_ONLY_NAMES = \[(.*?)\];", BUNDLE_SCAN.read_text(encoding="utf-8"), re.S)
    assert listing is not None, "scripts/check-bundle-secrets.mjs no longer has a SERVER_ONLY_NAMES array"
    return set(re.findall(r'"([A-Z0-9_]+)"', listing.group(1)))


def settings_problems(text: str, entries: dict[str, Entry]) -> list[str]:
    """Every setting the API reads is in the file, tagged for the API."""
    found = []
    platform = listed_under(text, PLATFORM_LEAD)
    for field, names in API.items():
        listed = [name for name in names if name in entries]
        for name in names:
            if name in PLATFORM and name not in platform:
                found.append(
                    f"{name}: Vercel sets it and the API reads it. List it in the header under '{PLATFORM_LEAD}'"
                )
        wanted = [name for name in names if name not in PLATFORM]
        if wanted and not listed:
            found.append(
                f"{wanted[0]}: the API reads it (setting {field}), but the file has no line for it. Add "
                f"'# > apps=api ...' and '{wanted[0]}=' in the section that fits"
            )
        for name in listed:
            tag = entries[name].tag
            if name in PLATFORM:
                found.append(f"{name}: Vercel sets it. Remove the line and leave the name in the header")
            elif tag is not None and tag.get("apps") not in {"api", "both"}:
                found.append(
                    f"{name}: tagged apps={tag.get('apps')}, but the API reads it (setting {field}). Use apps=both, "
                    "or apps=api if the web app does not read it"
                )
        for name in names:
            if listed and name not in listed and name not in PLATFORM and not mentioned(text, name):
                found.append(
                    f"{name}: the API also accepts it for {listed[0]}, but the file never names it. Say so in the "
                    f"comment above {listed[0]}"
                )
    return found


def entry_problems(entries: dict[str, Entry]) -> list[str]:
    """Every variable the file tags for the API, or for the maintenance commands, is read by it."""
    found = []
    for name, entry in entries.items():
        if name in TOOLING:
            continue
        if entry.tag is None:
            found.append(f"{name}: no tag line. Put '# > apps=... kind=... need=... scopes=...' just above it")
            continue
        apps = entry.tag.get("apps")
        if apps in {"api", "both"} and name not in API_NAMES:
            found.append(
                f"{name}: tagged apps={apps}, but no API setting reads that name. Remove the line, or fix the tag"
            )
        if apps == "cli" and name not in DATABASE_NAMES:
            found.append(
                f"{name}: tagged apps=cli, but no maintenance command reads that name. Remove the line, or fix the tag"
            )
        if entry.tag.get("baked") and apps not in {"web", "both"}:
            found.append(f"{name}: 'baked' means the web build fixes it, but the tag says apps={apps}")
    return found


def secret_problems(entries: dict[str, Entry], scanned: set[str]) -> list[str]:
    """Every secret is tagged kind=secret, and its value is one the bundle scan compares (ENV-07)."""
    found = [
        f"{name}: tagged kind=secret. Add it to SERVER_ONLY_NAMES in scripts/check-bundle-secrets.mjs"
        for name, entry in entries.items()
        if entry.tag and entry.tag.get("kind") == "secret" and name not in scanned
    ]
    for model, names in ((Settings, API), (DatabaseSettings, DATABASE)):
        for field in secret_fields(model):
            field_names = set(names[field])
            if not scanned & field_names and not NOT_SCANNED & field_names:
                found.append(
                    f"{names[field][0]}: a secret setting. Add it to SERVER_ONLY_NAMES in scripts/check-bundle-secrets.mjs"
                )
            entry = next((entries[name] for name in names[field] if name in entries), None)
            kind = entry.tag.get("kind") if entry and entry.tag else None
            if entry and entry.tag and kind != "secret" and not NOT_SECRET.keys() & field_names:
                found.append(
                    f"{entry.name}: a secret setting ({field}), but tagged kind={kind}. Tag it kind=secret, so the "
                    "deployment guide says to store it as a secret variable"
                )
    return found


def problems(text: str, scanned: Iterable[str] | None = None) -> list[str]:
    entries, found = parse(text)
    scanned = set(scanned) if scanned is not None else scanned_names()
    return [*found, *settings_problems(text, entries), *entry_problems(entries), *secret_problems(entries, scanned)]


TEXT = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")


def test_env_example_and_the_api_settings_agree() -> None:
    found = problems(TEXT)
    assert not found, "\n" + "\n".join(found)


@pytest.mark.parametrize(("name", "path"), [(name, path) for name, (path, _) in TOOLING.items()])
def test_the_tooling_exceptions_are_still_read_where_they_say(name: str, path: str) -> None:
    assert name in (REPO_ROOT / path).read_text(encoding="utf-8"), (
        f"{path} no longer reads {name}: drop it from TOOLING"
    )


def test_the_exceptions_are_still_exceptions() -> None:
    """A stale allowlist entry would hide the day the variable stops being one."""
    entries, _ = parse(TEXT)
    secret_names = {name for field in secret_fields(Settings) for name in API[field]}
    assert not PLATFORM - listed_under(TEXT, PLATFORM_LEAD), "the header lists every platform name the API reads"
    assert PLATFORM <= API_NAMES, "every platform name is still read by the API"
    assert not TOOLING.keys() & API_NAMES, "a tooling variable is now an API setting: tag it instead"
    assert NOT_SECRET.keys() <= secret_names, "a NOT_SECRET entry is no longer a SecretStr setting"
    for name in NOT_SECRET:
        tag = entries[name].tag
        assert tag is not None, f"{name} has a tag"
        assert tag["kind"] == "config", f"{name} is tagged kind=config"
    assert not NOT_SCANNED & scanned_names(), "a NOT_SCANNED name is now in the bundle scan's list"
    assert NOT_SECRET.keys() >= NOT_SCANNED


def without(name: str) -> Callable[[str], str]:
    """The text without a variable's line and the tag line above it."""
    return lambda text: re.sub(rf"(?:# > [^\n]*\n)?(?:# )?{name}=[^\n]*\n", "", text, count=1)


def replaced(old: str, new: str) -> Callable[[str], str]:
    def edit(text: str) -> str:
        assert old in text, f"{old!r} is not in .env.example any more: update this test"
        return text.replace(old, new, 1)

    return edit


CRON = "# > apps=api kind=secret need=optional scopes=production\nCRON_SECRET="
DRIFT = [
    ("a setting missing", without("CRON_SECRET"), "CRON_SECRET: the API reads it (setting cron_secret)"),
    ("an advanced setting missing", without("OUTBOX_LEASE_SECONDS"), "OUTBOX_LEASE_SECONDS: the API reads it"),
    ("a commented setting missing", without("CLERK_API_URL"), "CLERK_API_URL: the API reads it"),
    (
        "a tag naming the wrong app",
        replaced(CRON, CRON.replace("apps=api", "apps=web")),
        "CRON_SECRET: tagged apps=web, but the API reads it",
    ),
    (
        "a tag line missing",
        replaced("# > apps=api kind=config need=mode scopes=all\nLLM_MODEL=", "LLM_MODEL="),
        "LLM_MODEL: no tag line",
    ),
    (
        "a variable nothing reads",
        replaced("CRON_SECRET=\n", "CRON_SECRET=\n# > apps=api kind=config need=optional scopes=all\nMADE_UP=1\n"),
        "MADE_UP: tagged apps=api, but no API setting reads that name",
    ),
    (
        "a command-line variable nothing reads",
        replaced(
            "MIGRATION_DATABASE_URL=\n",
            "MIGRATION_DATABASE_URL=\n# > apps=cli kind=config need=optional scopes=not-on-vercel\nMADE_UP=1\n",
        ),
        "MADE_UP: tagged apps=cli",
    ),
    (
        "a tag with a typo",
        replaced(CRON, CRON.replace("kind=secret", "kind=secrt")),
        "CRON_SECRET: the tag has 'kind=secrt'",
    ),
    (
        "a tag without a field",
        replaced(CRON, CRON.replace(" need=optional", "")),
        "CRON_SECRET: the tag lacks need=",
    ),
    (
        "a variable listed twice",
        replaced(
            "CRON_SECRET=\n", "CRON_SECRET=\n# > apps=api kind=secret need=optional scopes=production\nCRON_SECRET=\n"
        ),
        "CRON_SECRET: listed twice",
    ),
    (
        "an alias the file does not name",
        replaced("The API also accepts the name CLERK_PUBLISHABLE_KEY for the publishable key.", "The API reads it."),
        "CLERK_PUBLISHABLE_KEY: the API also accepts it for NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
    ),
    (
        "a platform variable assigned",
        replaced("CRON_SECRET=\n", "CRON_SECRET=\nVERCEL_URL=\n"),
        "VERCEL_URL: Vercel sets it. Remove the line",
    ),
    (
        "a platform variable missing from the header",
        replaced("VERCEL_BRANCH_URL, VERCEL_GIT_COMMIT_SHA.", "VERCEL_BRANCH_URL."),
        "VERCEL_GIT_COMMIT_SHA: Vercel sets it and the API reads it. List it in the header",
    ),
    (
        "baked on an API-only variable",
        replaced(CRON, CRON.replace("secret ", "secret baked ")),
        "CRON_SECRET: 'baked' means the web build fixes it",
    ),
    (
        "a secret tagged as plain configuration",
        replaced(CRON, CRON.replace("kind=secret", "kind=config")),
        "CRON_SECRET: a secret setting (cron_secret), but tagged kind=config",
    ),
]


@pytest.mark.parametrize(("edit", "expected"), [case[1:] for case in DRIFT], ids=[case[0] for case in DRIFT])
def test_each_kind_of_drift_is_reported_by_name(edit: Callable[[str], str], expected: str) -> None:
    found = problems(edit(TEXT))
    assert any(expected in line for line in found), found


def test_a_secret_the_bundle_scan_does_not_compare_is_reported() -> None:
    found = problems(TEXT, scanned_names() - {"CRON_SECRET", "LLM_API_KEY"})
    assert any("CRON_SECRET: tagged kind=secret. Add it to SERVER_ONLY_NAMES" in line for line in found), found
    assert any("LLM_API_KEY: a secret setting. Add it to SERVER_ONLY_NAMES" in line for line in found), found
