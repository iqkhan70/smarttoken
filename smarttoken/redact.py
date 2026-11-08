"""Mask obvious secrets before a packet leaves the machine."""

from __future__ import annotations

import re

PEM_BLOCK = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
ASSIGNED_SECRET = re.compile(
    r"(?i)\b([A-Za-z0-9_]*?(?:api[_-]?key|secret|password|passwd|token|access[_-]?key))\b\s*[:=]\s*['\"][^'\"]{8,}['\"]"
)
OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9-]{20,}\b")
AWS_KEY = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]{12,}")


def redact(text: str) -> str:
    out = PEM_BLOCK.sub("[REDACTED]", text)
    out = ASSIGNED_SECRET.sub(lambda m: _redact_assignment(m.group(0)), out)
    out = OPENAI_KEY.sub("[REDACTED]", out)
    out = AWS_KEY.sub("[REDACTED]", out)
    out = BEARER.sub("Bearer [REDACTED]", out)
    return out


def redact_preserving_lines(text: str) -> str:
    """Redact secrets while keeping source line numbers stable for snippet ranges."""
    out = PEM_BLOCK.sub(
        lambda match: re.sub(r"[^\r\n]+", "[REDACTED]", match.group(0)),
        text,
    )
    out = ASSIGNED_SECRET.sub(lambda m: _redact_assignment(m.group(0)), out)
    out = OPENAI_KEY.sub("[REDACTED]", out)
    out = AWS_KEY.sub("[REDACTED]", out)
    out = BEARER.sub("Bearer [REDACTED]", out)
    return out


def _redact_assignment(chunk: str) -> str:
    if "=" in chunk:
        left, _right = chunk.split("=", 1)
        return f"{left}= [REDACTED]"
    if ":" in chunk:
        left, _right = chunk.split(":", 1)
        return f"{left}: [REDACTED]"
    return "[REDACTED]"
