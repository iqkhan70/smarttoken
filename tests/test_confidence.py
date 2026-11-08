from pathlib import Path

from typer.testing import CliRunner

from smarttoken.cli import app
from smarttoken.confidence import assess
from smarttoken.index_store import IndexStore
from smarttoken.search import search_index

runner = CliRunner()


def _assess(store: IndexStore, query: str):
    return assess(store, query, search_index(store, query, limit=20))


def test_answerable_question_is_high(indexed: tuple[IndexStore, str]) -> None:
    store, _ = indexed
    result = _assess(store, "where is the webhook signature checked?")
    assert result.level == "HIGH"
    assert result.signals.missing_terms == []


def test_absent_topic_is_low_and_names_missing_words(indexed: tuple[IndexStore, str]) -> None:
    store, _ = indexed
    result = _assess(store, "where is the kafka consumer for graphql events")
    assert result.level == "LOW"
    assert "kafka" in result.signals.missing_terms
    assert any("kafka" in reason for reason in result.reasons)


def test_stemmed_word_is_not_missing(indexed: tuple[IndexStore, str]) -> None:
    store, _ = indexed
    result = _assess(store, "verifying webhook signatures")
    assert "signatures" not in result.signals.missing_terms


def test_check_and_measure_not_checked(tmp_path: Path, sample_root: Path) -> None:
    base = ["--home", str(tmp_path / "home")]
    runner.invoke(app, [*base, "add", str(sample_root)])
    runner.invoke(app, [*base, "index"])

    checked = runner.invoke(app, [*base, "check", "where is the webhook signature checked?"])
    assert checked.exit_code == 0, checked.stdout
    assert "Confidence: HIGH" in checked.stdout
    assert "Pack:" in checked.stdout
    assert "Saved:" in checked.stdout

    low = runner.invoke(app, [*base, "check", "where is the kafka consumer for graphql events", "--copy"])
    assert low.exit_code == 0
    assert "Not copied: confidence is LOW" in low.stdout

    measured = runner.invoke(app, [*base, "measure", "where is the webhook signature checked?"])
    assert measured.exit_code == 0
    assert "NOT CHECKED" in measured.stdout
