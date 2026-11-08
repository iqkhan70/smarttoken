"""Paths and settings. Default home is ~/.smarttoken; override with --home or SMARTTOKEN_HOME."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import List

from pydantic import BaseModel, Field


class Settings(BaseModel):
    default_budget: int = Field(default=4000, ge=200)
    max_file_bytes: int = Field(default=512_000, ge=1024)
    context_lines: int = Field(default=12, ge=2, le=80)
    encoding: str = "cl100k_base"
    search_limit: int = Field(default=15, ge=1, le=100)
    dollars_per_million_input: float = Field(default=3.0, ge=0)
    # `smarttoken "task"`: confirm at each tier; above the last one, ask to narrow the prompt.
    ask_tiers: List[int] = Field(default_factory=lambda: [3500, 5000, 7000])
    # Where `smarttoken "task"` sends the prompt: clipboard, print, vscode, cursor, cursor-agent, copilot, claude.
    send_to: str = "clipboard"
    # Optional model for CLIs that take one (cursor-agent, claude, copilot).
    send_model: str = ""


class AppPaths:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.config_file = home / "config.toml"
        self.db_file = home / "index.db"
        self.usage_file = home / "usage.jsonl"
        self.history_file = home / "history.jsonl"
        self.focus_file = home / "focus.json"
        self.packs_dir = home / "packs"

    def ensure_home(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)


def resolve_home(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    env = os.environ.get("SMARTTOKEN_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".smarttoken"


def load_settings(paths: AppPaths) -> Settings:
    if not paths.config_file.exists():
        return Settings()
    data = tomllib.loads(paths.config_file.read_text(encoding="utf-8"))
    return Settings.model_validate(data)


def save_settings(paths: AppPaths, settings: Settings) -> None:
    paths.ensure_home()
    paths.config_file.write_text(
        (
            f"default_budget = {settings.default_budget}\n"
            f"max_file_bytes = {settings.max_file_bytes}\n"
            f"context_lines = {settings.context_lines}\n"
            f'encoding = "{settings.encoding}"\n'
            f"search_limit = {settings.search_limit}\n"
            f"dollars_per_million_input = {settings.dollars_per_million_input}\n"
            f"ask_tiers = [{', '.join(str(t) for t in settings.ask_tiers)}]\n"
            f'send_to = "{settings.send_to}"\n'
            f'send_model = "{settings.send_model}"\n'
        ),
        encoding="utf-8",
    )
