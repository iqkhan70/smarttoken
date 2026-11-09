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


LINE_SEP = r"\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]"


def _mask_lines(chunk: str) -> str:
    return "".join(
        "[REDACTED]" if part and not re.fullmatch(LINE_SEP, part) else part
        for part in re.split(f"({LINE_SEP})", chunk)
    )


def _keep_lines(chunk: str, replacement: str) -> str:
    """Put the replacement on the first line and mask later lines in place."""
    replacement = re.sub(LINE_SEP, " ", replacement)
    parts = re.split(f"({LINE_SEP})", chunk)
    out: list[str] = []
    first = True
    for part in parts:
        if part and not re.fullmatch(LINE_SEP, part):
            out.append(replacement if first else "[REDACTED]")
            first = False
        else:
            out.append(part)
    return "".join(out)


def _redact_assignment(chunk: str) -> str:
    match = re.search(r"[:=]", chunk)
    if match is None:
        return "[REDACTED]"
    left = re.sub(LINE_SEP, "", chunk[: match.start()])
    return f"{left}{match.group(0)} [REDACTED]"
