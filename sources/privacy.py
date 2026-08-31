"""Shared deterministic private-marker detection and redaction."""

from __future__ import annotations

import re
from collections.abc import Iterable

EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9.!#$%&'*+=?^_`{|}~-]+@"
    r"(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}\b"
)
HOME_PATH_RE = re.compile(
    r"(?<![\w.])/(?:Users|home)/[^/\s<>()[\]{}\"']+"
    r"(?:/[^\s<>()[\]{}\"']+)*"
)
KEY_PREFIX_RE = re.compile(
    r"\b(?:"
    r"sk-[A-Za-z0-9_-]{16,}|"
    r"brv-[A-Za-z0-9_-]{16,}|"
    r"ghp_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{16,}|"
    r"AKIA[A-Z0-9]{16}|"
    r"BSA[A-Za-z0-9_-]{16,}"
    r")\b"
)
KEY_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([A-Za-z0-9_]*(?:api[_ -]?key|access[_ -]?token|auth[_ -]?token|"
    r"secret|password))\b(\s*[:=]\s*)([^\s,;]+)"
)
PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?"
    r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)


def find_private_markers(text: str) -> list[str]:
    """Return stable marker categories found in text (never secret values)."""
    found: list[str] = []
    checks: Iterable[tuple[str, re.Pattern[str]]] = (
        ("email", EMAIL_RE),
        ("home-path", HOME_PATH_RE),
        ("key-prefix", KEY_PREFIX_RE),
        ("key-assignment", KEY_ASSIGNMENT_RE),
        ("private-key", PRIVATE_KEY_RE),
    )
    for label, pattern in checks:
        if pattern.search(text):
            found.append(label)
    return found


def redact_text(text: str) -> tuple[str, dict[str, int]]:
    """Redact private identifiers while preserving surrounding markdown."""
    counts: dict[str, int] = {}

    def sub(pattern: re.Pattern[str], replacement, label: str, value: str) -> str:
        def repl(match: re.Match[str]) -> str:
            counts[label] = counts.get(label, 0) + 1
            return replacement(match) if callable(replacement) else replacement

        return pattern.sub(repl, value)

    text = sub(PRIVATE_KEY_RE, "[redacted-private-key]", "private-key", text)
    text = sub(EMAIL_RE, "[redacted-email]", "email", text)
    text = sub(HOME_PATH_RE, "[redacted-home-path]", "home-path", text)
    text = sub(KEY_PREFIX_RE, "[redacted-key]", "key-prefix", text)
    text = sub(
        KEY_ASSIGNMENT_RE,
        lambda match: f"{match.group(1)}{match.group(2)}[redacted-secret]",
        "key-assignment",
        text,
    )
    return text, counts
