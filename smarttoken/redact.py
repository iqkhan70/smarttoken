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
    """Mask secrets without changing the number of lines."""
    out = PEM_BLOCK.sub(lambda m: _mask_lines(m.group(0)), text)
    out = ASSIGNED_SECRET.sub(lambda m: _keep_lines(m.group(0), _redact_assignment(m.group(0))), out)
    out = OPENAI_KEY.sub("[REDACTED]", out)
    out = AWS_KEY.sub("[REDACTED]", out)
    out = BEARER.sub(lambda m: _keep_lines(m.group(0), "Bearer [REDACTED]"), out)
    return out


def redact_preserving_lines(text: str) -> str:
    """Redact secrets while keeping source line numbers stable for snippet ranges."""
    return redact(text)


def _mask_lines(chunk: str) -> str:
    return re.sub(r"[^\r\n]+", "[REDACTED]", chunk)


def _keep_lines(chunk: str, replacement: str) -> str:
    return replacement + "".join(re.findall(r"\r\n|\r|\n", chunk))


def _redact_assignment(chunk: str) -> str:
    if "=" in chunk:
        left, _right = chunk.split("=", 1)
        return f"{left}= [REDACTED]"
    if ":" in chunk:
        left, _right = chunk.split(":", 1)
        return f"{left}: [REDACTED]"
    return "[REDACTED]"
