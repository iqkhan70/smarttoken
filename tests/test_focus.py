from pathlib import Path

import pytest
from typer.testing import CliRunner

from smarttoken import focus
from smarttoken.cli import app
from smarttoken.config import AppPaths, Settings
from smarttoken.index_store import IndexStore
from smarttoken.registry import add_paths
from smarttoken.search import search_index

runner = CliRunner()

PORTAL = {
    "src/app/aops/AopListComponent.tsx": "export function AopListComponent() {\n  // list of AOPs with status column, sort by due date, filter by owner\n}\n",
    "src/app/aops/aopService.ts": "export async function fetchAops(filter) {\n  // load AOP list with status and owner filter\n}\n",
    "src/app/aops/AopDetailPage.tsx": "export function AopDetailPage() {\n  // AOP detail with status history and owner\n}\n",
    "src/app/aops/AopFilters.tsx": "export function AopFilters() {\n  // status filter, owner filter, date range filter for the list\n}\n",
    "src/app/cases/CaseListComponent.tsx": "export function CaseListComponent() {\n  // list of cases with status column, priority badge, sort by created date\n}\n",
    "src/app/cases/caseService.ts": "export async function fetchCases(filter) {\n  // load case list with status filter and priority\n}\n",
    "src/app/cases/CaseDetailPage.tsx": "export function CaseDetailPage() {\n  // case detail with status history and assignee\n}\n",
    "src/app/cases/CaseFilters.tsx": "export function CaseFilters() {\n  // status filter, priority filter, assignee filter for the case list\n}\n",
    "src/app/shared/Table.tsx": "export function Table() {\n  // generic list table: sort, filter, paging\n}\n",
    "src/app/shared/StatusBadge.tsx": "export function StatusBadge() { /* colored badge for status */ }\n",
}


@pytest.fixture
def portal(tmp_path: Path) -> tuple[AppPaths, IndexStore, Path]:
    root = tmp_path / "portal"
    for rel, text in PORTAL.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    paths = AppPaths(tmp_path / "home")
    paths.ensure_home()
    store = IndexStore(paths.db_file)
    repos = add_paths(store, [root])
    store.index_repo(repos[0], Settings())
    return paths, store, root


def _ask(paths: AppPaths, store: IndexStore, query: str):
    hits = search_index(store, query, limit=20)
    drift = focus.check_drift(paths, store, query, hits, search_fn=lambda text: search_index(store, text, limit=20))
    drift_to = drift.new_words[0] if drift and not drift.asked_for_new_area else None
    focus.record(paths, "check", query, hits, drift_to=drift_to)
    return drift


def test_path_words_split_folders_and_camel_case() -> None:
    assert focus.path_words("src/app/aops/AopListComponent.tsx") == ["src", "app", "aop", "list", "component"]
    assert "status" in focus.path_words("shared/StatusBadge.tsx")


def test_question_landing_on_cases_after_aop_work_is_flagged(portal) -> None:
    paths, store, _ = portal
    assert _ask(paths, store, "add sort by due date to the aop list") is None
    assert _ask(paths, store, "show owner avatar in the aop list") is None
    drift = _ask(paths, store, "add a priority column to the list")
    assert drift is not None
    assert drift.focus_words[0] == "aop"
    assert drift.new_words[0] == "case"
    assert drift.suggestion is not None and drift.suggestion.rel_path.endswith("AopListComponent.tsx")
    assert not drift.asked_for_new_area


def test_staying_in_area_or_naming_it_is_not_flagged(portal) -> None:
    paths, store, _ = portal
    for query in ["aop status update api", "aop owner filter", "aop detail page", "fetch aops with filters"]:
        assert _ask(paths, store, query) is None
    assert _ask(paths, store, "aop list should show status badge") is None


def test_naming_the_new_area_is_marked_intended(portal) -> None:
    paths, store, _ = portal
    _ask(paths, store, "add sort by due date to the aop list")
    _ask(paths, store, "aop list should show status badge")
    drift = _ask(paths, store, "update the case list columns")
    assert drift is not None and drift.asked_for_new_area


def test_one_accident_keeps_focus_but_a_real_switch_moves_it(portal) -> None:
    paths, store, _ = portal
    _ask(paths, store, "add sort by due date to the aop list")
    _ask(paths, store, "show owner avatar in the aop list")
    assert _ask(paths, store, "add a priority column to the list") is not None
    assert _ask(paths, store, "add a priority column to the list") is not None
    assert _ask(paths, store, "show the assignee on the case detail page") is not None
    assert _ask(paths, store, "add a priority filter for cases") is None


def test_pinned_focus_flags_without_history(portal) -> None:
    paths, store, _ = portal
    focus.pin(paths, "aop")
    drift = _ask(paths, store, "add a priority column to the list")
    assert drift is not None and drift.new_words[0] == "case"
    assert _ask(paths, store, "add a priority column to the aop list") is None


def test_cli_check_copy_is_held_back_on_drift(portal) -> None:
    paths, _, _ = portal
    base = ["--home", str(paths.home)]
    runner.invoke(app, [*base, "check", "add sort by due date to the aop list"])
    runner.invoke(app, [*base, "check", "show owner avatar in the aop list"])

    shown = runner.invoke(app, [*base, "focus"])
    assert "aop" in shown.stdout

    result = runner.invoke(app, [*base, "check", "add a priority column to the list", "--copy"])
    assert result.exit_code == 0, result.stdout
    assert "Focus check" in result.stdout
    assert "Not copied: the focus check warned" in result.stdout

    packed = runner.invoke(app, [*base, "pack", "add a priority column to the list", "--copy"])
    assert packed.exit_code == 3

    assert runner.invoke(app, [*base, "focus", "--clear"]).exit_code == 0
    assert not paths.history_file.exists()
