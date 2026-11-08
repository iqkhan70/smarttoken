from smarttoken.config import Settings
from smarttoken.rank import content_terms, score_hit
from smarttoken.repo_map import render_map
from smarttoken.search import fts_query, search_index


def test_map_reads_readme_and_entrypoints(indexed) -> None:
    store, name = indexed
    repo = store.require_repo(name)
    text = render_map(store, repo)
    assert "payments-sample" in text or "Accepts payment webhooks" in text
    assert "src/app.py" in text
    assert "docs/deploy.md" in text
    assert "dist/bundle.js" not in text


def test_fts_drops_stopwords_and_task_verbs() -> None:
    query = "where is CreatePaymentIntentAsync implemented for Stripe"
    terms = content_terms(query)
    assert "implemented" not in terms
    assert "where" not in terms
    assert "createpaymentintentasync" in terms
    assert "stripe" in terms
    fts = fts_query(query).lower()
    assert "implemented" not in fts
    assert "createpaymentintentasync" in fts


def test_fix_scripts_rank_below_payment_retry() -> None:
    query = "fix retry on Stripe payment capture"
    retry = score_hit(query, "src/apps/MobileApp/utils/orderPaymentRetry.ts", "source", 8.0)
    script = score_hit(query, "deploy/digitalocean/scripts/fix-catalog-migrations.sh", "config", 8.0)
    assert retry > script


def test_search_finds_webhook_signature(indexed) -> None:
    store, name = indexed
    hits = search_index(store, "webhook signature", repo_name=name, limit=10)
    paths = [hit.rel_path for hit in hits]
    assert any(path.endswith("webhook.py") for path in paths)
    assert hits[0].rel_path.endswith("webhook.py") or "webhook" in hits[0].preview.lower()


def test_incremental_index_skips_unchanged(indexed) -> None:
    store, name = indexed
    repo = store.require_repo(name)
    stats = store.index_repo(repo, Settings())
    assert stats.written == 0
    assert stats.unchanged >= 5


def test_task_kind_change_words_at_word_start() -> None:
    from smarttoken.rank import task_kind

    assert task_kind("Change it so a rating outside 1 to 5 is rejected") == "enhance"
    assert task_kind("validate the email on signup") == "enhance"
    assert task_kind("where is the shipping address stored") == "understand"
    assert task_kind("fix the failing webhook") == "fix"


def test_quoted_phrases() -> None:
    from smarttoken.rank import quoted_phrases

    assert quoted_phrases('return "Rating must be between 1 and 5" on bad input') == ["rating must be between 1 and 5"]
    assert quoted_phrases("don't change what isn't broken") == []
    assert quoted_phrases("show `PaymentIntent created` in the log") == ["paymentintent created"]
    assert quoted_phrases('a "single" word is not a phrase') == []


def test_sensitive_files_never_returned() -> None:
    from smarttoken.rank import is_sensitive_path

    for path in ("app/keystore.release.txt", ".env", "config/.env.production", "certs/server.pem",
                 "deploy/credentials.json", "secrets.yaml"):
        assert is_sensitive_path(path), path
    for path in (".env.example", "src/SecretManager.cs", "pkg/credentials/provider.go", "README.md"):
        assert not is_sensitive_path(path), path


def test_quoted_phrase_lifts_file_with_literal(indexed) -> None:
    store, _ = indexed
    from smarttoken.search import PHRASE_BONUS

    def auth_score(query: str) -> float:
        return next(hit.score for hit in search_index(store, query, limit=20) if hit.rel_path.endswith("auth.py"))

    lift = auth_score('where are the "payment admin routes" checked') - auth_score("where are the payment admin routes checked")
    assert lift == PHRASE_BONUS
