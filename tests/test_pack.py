from smarttoken.config import Settings
from smarttoken.pack import build_packet
from smarttoken.redact import redact


def test_redact_masks_keys() -> None:
    raw = (
        'API_KEY = "sk-test-not-real-abcdefghijklmnopqrstuvwxyz"\n'
        "AKIAIOSFODNN7EXAMPLE\n"
        "Authorization: Bearer supersecrettokenvalue\n"
    )
    out = redact(raw)
    assert "sk-test-not-real" not in out
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "supersecrettokenvalue" not in out
    assert "[REDACTED]" in out


def test_pack_stays_under_budget_and_redacts(indexed) -> None:
    store, name = indexed
    packet = build_packet(
        store,
        "fix retry on payment webhook signature",
        Settings(),
        repo_name=name,
        budget=800,
    )
    assert packet.used_tokens <= 800
    assert "webhook" in packet.markdown.lower()
    assert "Suggested Cursor ask" in packet.markdown
    assert "sk-test-not-real" not in packet.markdown
    assert "AKIAIOSFODNN7EXAMPLE" not in packet.markdown
    assert "dist/bundle.js" not in packet.markdown or "Do not open" in packet.markdown

    secrets = build_packet(
        store,
        "API_KEY secrets_demo",
        Settings(),
        repo_name=name,
        budget=600,
    )
    assert "sk-test-not-real" not in secrets.markdown
    assert "AKIAIOSFODNN7EXAMPLE" not in secrets.markdown
    assert "secrets_demo.py" in secrets.markdown


def test_overlapping_windows_are_dropped() -> None:
    from smarttoken.pack import _hit_windows

    content = "\n".join(f"rating line {i}" if i % 3 == 0 else f"other {i}" for i in range(80))
    windows = _hit_windows(content, "rating", context_lines=12)
    spans = [(start, end) for start, end, _, _ in windows]
    for i, a in enumerate(spans):
        for b in spans[i + 1:]:
            shared = min(a[1], b[1]) - max(a[0], b[0]) + 1
            assert shared <= 0.4 * min(a[1] - a[0] + 1, b[1] - b[0] + 1), (a, b)


def test_strong_files_each_get_a_window_first() -> None:
    from smarttoken.models import Snippet
    from smarttoken.pack import _breadth_first

    def snip(path: str, score: float, start: int) -> Snippet:
        return Snippet("r", path, start, start + 5, "x", "why", score, "source", "python")

    ordered = _breadth_first(
        [snip("top.py", 100, 1), snip("top.py", 100, 30), snip("top.py", 100, 60),
         snip("second.py", 90, 1), snip("weak.py", 10, 1)]
    )
    assert [(s.rel_path, s.start_line) for s in ordered[:2]] == [("top.py", 1), ("second.py", 1)]
    assert ordered[-1].rel_path == "weak.py"


def test_pointers_skip_weak_files_and_code() -> None:
    from smarttoken.models import Snippet
    from smarttoken.pack import render_pointers, select_pointers

    def snip(path: str, score: float, start: int, why: str) -> Snippet:
        return Snippet("r", path, start, start + 20, "secret source line", why, score, "source", "python")

    chosen = select_pointers(
        [snip("top.py", 100, 10, "method `Update`"), snip("top.py", 100, 40, "method `Create`"),
         snip("second.py", 80, 5, "method `Save`"), snip("weak.py", 10, 1, "method `Noise`")],
        task="where is the webhook signature checked",
    )
    assert [s.rel_path for s in chosen] == ["top.py", "second.py", "top.py"]
    text = render_pointers(chosen)
    assert "secret source line" not in text
    assert "L10-30" in text
    assert "weak.py" not in text


def test_redact_preserves_line_count_for_multiline_matches() -> None:
    from smarttoken.redact import redact

    text = 'api_key:\n  "supersecretvalue123"\nBearer\nplain\nafter'
    assert redact(text).count("\n") == text.count("\n")
    assert "supersecretvalue123" not in redact(text)


def test_redact_preserves_splitlines_alignment() -> None:
    from smarttoken.redact import redact

    for text in [
        'api_key:\n  "supersecretvalue123"',
        'api_key\n= "supersecretvalue123"\n',
        'a\x0bapi_key = "supersecretvalue123"\x0cb',
        '-----BEGIN PRIVATE KEY-----\x85body\u2028-----END PRIVATE KEY-----',
    ]:
        out = redact(text)
        assert len(out.splitlines()) == len(text.splitlines())
        assert "supersecretvalue123" not in out and "body" not in out
