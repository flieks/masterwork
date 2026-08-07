"""Mask secret-looking values in backend-built prompt content.

`redact()` runs over file-derived text (asset titles, descriptions, paths, ids)
before it is embedded into a `claude -p` prompt, so a secret accidentally pasted
into a skill/agent file is not shipped to the LLM provider verbatim.

Limitation: prompts often only *point* claude at asset files (simulations,
diagram generation) and the CLI then reads them itself with its Read tool —
that content never passes through the backend and cannot be redacted here.
This module covers the interceptable surface only: text the backend embeds.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_PEM_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
# Payload segment must also start `eyJ` (base64 of `{"`) — strong guard against prose.
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\b")
_AWS_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_GITHUB_RE = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b")
_SK_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")
_STRIPE_RE = re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b")

_SIMPLE_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("private-key", _PEM_RE),
    ("jwt", _JWT_RE),
    ("aws-access-key", _AWS_RE),
    ("github-token", _GITHUB_RE),
    ("sk-key", _SK_RE),
    ("stripe-key", _STRIPE_RE),
]

# scheme://user:password@ — password may not contain `/` so URL paths with an
# `@` later in them (callback?email=a@b) don't false-positive.
_URL_CRED_RE = re.compile(
    r"(?P<prefix>\b[a-z][a-z0-9+.-]*://[^:/\s@]*:)(?P<password>[^@/\s\[\]]+)@"
)

# Candidate `name = value` / `name: value` pairs; kept broad, then filtered by
# _is_secret_name and _looks_secret so prose and code don't get mangled.
_ASSIGN_RE = re.compile(
    r"(?P<key>\b[A-Za-z][A-Za-z0-9_.-]{0,60})"
    r"(?P<sep>\s*(?::=|=>|[:=])\s*)"
    r"(?P<quote>[\"']?)(?P<value>[A-Za-z0-9+/=_.~-]{16,})"
)
_SECRET_SEGMENTS = frozenset(
    {
        "apikey",
        "key",
        "secret",
        "secrets",
        "token",
        "tokens",
        "passwd",
        "password",
        "credential",
        "credentials",
    }
)
_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_PLACEHOLDER_RE = re.compile(r"(?i)example|sample|placeholder|changeme|dummy|your[_-]|xxxx")


def _entropy(value: str) -> float:
    counts = Counter(value)
    total = len(value)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def _is_secret_name(key: str) -> bool:
    """True if any snake/kebab/camel segment of `key` is a secret-ish word."""
    segments = re.split(r"[_.\-]", _CAMEL_SPLIT_RE.sub("_", key).lower())
    return not _SECRET_SEGMENTS.isdisjoint(segments)


def _looks_secret(value: str) -> bool:
    """High-entropy gate: real keys, not slugs like `my-secret-name` or prose."""
    if len(value) < 16 or _PLACEHOLDER_RE.search(value):
        return False
    classes = sum(
        (
            any(c.islower() for c in value),
            any(c.isupper() for c in value),
            any(c.isdigit() for c in value),
            any(not c.isalnum() for c in value),
        )
    )
    if classes >= 3:
        return True
    return any(c.isdigit() for c in value) and _entropy(value) >= 3.5


def _assign_sub(match: re.Match[str]) -> str:
    if not _is_secret_name(match.group("key")) or not _looks_secret(match.group("value")):
        return match.group(0)
    prefix = match.group("key") + match.group("sep") + match.group("quote")
    return f"{prefix}[REDACTED:secret-assignment]"


def redact(text: str) -> str:
    """Replace secret-looking values with `[REDACTED:<kind>]` markers."""
    for kind, pattern in _SIMPLE_RULES:
        text = pattern.sub(f"[REDACTED:{kind}]", text)
    text = _URL_CRED_RE.sub(lambda m: f"{m.group('prefix')}[REDACTED:url-credentials]@", text)
    return _ASSIGN_RE.sub(_assign_sub, text)
