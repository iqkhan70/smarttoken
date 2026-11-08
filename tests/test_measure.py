from smarttoken.config import Settings
from smarttoken.eval_tasks import load_tasks
from smarttoken.measure import _path_in, bundled_tasks_path, measure_task


def test_measure_saves_tokens_and_hits_gold(indexed) -> None:
    store, name = indexed
    result = measure_task(
        store,
        "where is the webhook signature checked?",
        Settings(),
        repo_name=name,
        budget=1200,
        must_include=["src/payments/webhook.py"],
        must_exclude=["dist/bundle.js"],
    )
    assert result.passed
    assert result.whole_repo and result.whole_repo.tokens > result.pack_tokens
    assert result.required_recall == 1.0
    assert result.savings_pct(result.whole_repo) > 0
    assert result.pack_tokens <= result.budget


def test_measure_fails_when_gold_missing(indexed) -> None:
    store, name = indexed
    result = measure_task(
        store,
        "where is the webhook signature checked?",
        Settings(),
        repo_name=name,
        must_include=["src/does/not/exist.py"],
    )
    assert result.passed is False
    assert result.required_recall == 0.0


def test_gold_path_matches_expo_brackets() -> None:
    haystack = "Eats/src/apps/TraditionalEats.MobileApp/app/restaurants/[restaurantId]/request-ride.tsx"
    needle = "src/apps/TraditionalEats.MobileApp/app/restaurants/[restaurantId]/request-ride.tsx"
    assert _path_in(needle, haystack)
    assert _path_in(needle, "Eats/src/apps/TraditionalEats.MobileApp/app/restaurants/request-ride.tsx")
    assert _path_in(needle, "... request-ride.tsx ...")


def test_bundled_tasks_load() -> None:
    tasks = load_tasks(bundled_tasks_path())
    assert {item.id for item in tasks} >= {"webhook-signature", "deploy-checkout"}


def test_folder_rule_matches_path_segment_not_code_words() -> None:
    assert not _path_in("obj/", "var account = stripeEvent.Data.Object as Account;")
    assert _path_in("obj/", "- `Eats/src/Api/obj/Debug/net10.0/Api.AssemblyInfo.cs`")
    assert _path_in("dist/", "`dist/bundle.js`")
