"""Token estimates for pack budgets."""

from __future__ import annotations

from functools import lru_cache

import tiktoken

# Used when tiktoken cannot fetch its encoding file (offline or blocked network).
CHARS_PER_TOKEN = 4


@lru_cache(maxsize=None)
def _encoding(encoding_name: str):
    for name in (encoding_name, "cl100k_base"):
        try:
            return tiktoken.get_encoding(name)
        except Exception:
            continue
    return None


def count_tokens(text: str, encoding_name: str = "cl100k_base") -> int:
    enc = _encoding(encoding_name)
    if enc is None:
        return -(-len(text) // CHARS_PER_TOKEN)
    return len(enc.encode(text))
