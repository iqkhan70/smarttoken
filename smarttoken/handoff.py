"""Send a finished prompt straight to an AI tool, so there's nothing to copy and paste."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple
from urllib.parse import quote

from smarttoken.clipboard import copy_text
from smarttoken.config import AppPaths

TARGETS = ("clipboard", "print", "vscode", "cursor", "cursor-agent", "copilot", "claude")
# These open files themselves, so the default prompt names locations and leaves the code out.
AGENT_TARGETS = ("vscode", "cursor-agent", "copilot", "claude")
PACK_STYLES = ("auto", "full", "pointer")
# Cursor rejects prompt deeplinks longer than this (whole URL, after encoding).
CURSOR_DEEPLINK_MAX = 10_000
CURSOR_DEEPLINK = "cursor://anysphere.cursor-deeplink/prompt?text="

# Binaries to try for each CLI target, in order.
_BINARIES = {
    "vscode": ("code",),
    "cursor-agent": ("cursor-agent", "agent"),
    "copilot": ("copilot",),
    "claude": ("claude",),
}


def choose_tier(tokens: int, tiers: Sequence[int]) -> Optional[Tuple[int, int]]:
    """(tier index, tier limit) for the smallest tier that fits; None when over the largest."""
    for index, limit in enumerate(sorted(tiers)):
        if tokens <= limit:
            return index, limit
    return None


def tier_message(tokens: int, index: int, limit: int, tier_count: int) -> str:
    if index == 0:
        return f"This will take ~{tokens} tokens."
    if index == tier_count - 1:
        return f"Large context detected. Requires {limit} tokens (~{tokens})."
    return f"Base limit exceeded. Requires {limit} tokens (~{tokens})."


def resolve_pack_style(requested: str, target: str) -> str:
    """auto: a pointer for agent CLIs, the full pack for paste/print/Cursor chat."""
    if requested not in PACK_STYLES:
        raise ValueError(f"Unknown pack style '{requested}'. Use one of: {', '.join(PACK_STYLES)}.")
    if requested == "auto":
        return "pointer" if target in AGENT_TARGETS else "full"
    return requested


def build_pointer_prompt(task: str, pointer_markdown: str, workspaces: List[str]) -> str:
    where = ", ".join(f"`{path}`" for path in workspaces) or "the registered repos"
    return (
        f"# Task\n{task}\n\n"
        f"Work in: {where}.\n"
        "Open the locations below first. Each line is a file and the line range where this task matches. "
        "Read those ranges, then make the smallest change that does the task. "
        "Open other files only if these are not enough, and say which ones and why.\n\n"
        f"{pointer_markdown}\n"
    )


def build_prompt(task: str, packet_markdown: str, workspaces: List[str]) -> str:
    where = ", ".join(f"`{path}`" for path in workspaces) or "the registered repos"
    return (
        f"# Task\n{task}\n\n"
        f"Work in: {where}.\n"
        "The Smart Token pack below lists the files, line ranges and snippets that matter. "
        "Start from those files. Open other files only if the pack is not enough, and say which ones and why. "
        "Keep changes small and inside the named files where possible.\n\n"
        f"{packet_markdown}\n"
    )


def save_prompt(paths: AppPaths, task: str, text: str) -> Path:
    paths.packs_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")[:40] or "task"
    path = paths.packs_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{slug}.md"
    path.write_text(text, encoding="utf-8")
    return path


def workspace_for(repo_paths: List[str]) -> Optional[str]:
    """The repo folder, or the common parent when the task spans several repos."""
    unique = sorted(set(repo_paths))
    if not unique:
        return None
    if len(unique) == 1:
        return unique[0]
    common = os.path.commonpath(unique)
    return common if common not in ("", os.sep) else unique[0]


@dataclass
class Handoff:
    target: str
    command: List[str] = field(default_factory=list)
    stdin: Optional[str] = None
    cwd: Optional[str] = None
    url: Optional[str] = None
    note: str = ""


def _binary(target: str) -> Optional[str]:
    for name in _BINARIES.get(target, ()):
        found = shutil.which(name)
        if found:
            return found
    return None


def plan(
    target: str,
    prompt: str,
    prompt_file: Path,
    task: str,
    workspace: Optional[str],
    model: str = "",
) -> Handoff:
    """What to run for a target. Raises ValueError for unknown targets or missing CLIs."""
    if target not in TARGETS:
        raise ValueError(f"Unknown target '{target}'. Use one of: {', '.join(TARGETS)}.")
    if target in ("clipboard", "print"):
        return Handoff(target)

    if target == "cursor":
        url = CURSOR_DEEPLINK + quote(prompt, safe="")
        note = "Review and send it in Cursor's chat."
        if len(url) > CURSOR_DEEPLINK_MAX:
            short = (
                f"{task}\n\nRead the Smart Token context pack at {prompt_file} first. "
                "It lists the files and line ranges to change. Then do the task."
            )
            url = CURSOR_DEEPLINK + quote(short, safe="")
            note = "The pack is too long for a Cursor link, so the prompt points Cursor at the saved pack file."
        return Handoff(target, url=url, note=note)

    exe = _binary(target)
    if exe is None:
        names = " or ".join(_BINARIES[target])
        raise ValueError(f"'{names}' not found on PATH. Install it, or use --to clipboard.")

    if target == "vscode":
        instruction = (
            f"{task}. Use the Smart Token context pack attached from stdin: it lists the files and line ranges to change."
        )
        return Handoff(target, [exe, "chat", "-m", "agent", instruction, "-"], stdin=prompt, cwd=workspace,
                       note="Opened in VS Code chat (Copilot agent mode).")
    if target == "cursor-agent":
        command = [exe]
        if workspace:
            command += ["--workspace", workspace]
        if model:
            command += ["--model", model]
        return Handoff(target, command + [prompt], cwd=workspace, note="Starting Cursor Agent in this terminal.")
    if target == "copilot":
        command = [exe] + (["--model", model] if model else []) + ["-i", prompt]
        return Handoff(target, command, cwd=workspace, note="Starting GitHub Copilot CLI in this terminal.")
    command = [exe] + (["--model", model] if model else []) + [prompt]
    return Handoff(target, command, cwd=workspace, note="Starting Claude Code in this terminal.")


def _open_url(url: str) -> bool:
    try:
        if sys.platform == "darwin":
            return subprocess.run(["open", url], check=False).returncode == 0
        if sys.platform.startswith("win"):
            os.startfile(url)  # type: ignore[attr-defined]
            return True
        opener = shutil.which("xdg-open")
        return bool(opener) and subprocess.run([opener, url], check=False).returncode == 0
    except OSError:
        return False


def execute(handoff: Handoff, prompt: str) -> Tuple[bool, str]:
    """Run the hand-off. Returns (ok, message)."""
    if handoff.target == "print":
        return True, ""
    if handoff.target == "clipboard":
        if copy_text(prompt):
            return True, "Copied to clipboard. Paste it into your AI chat."
        return False, "Clipboard copy failed (no pbcopy/wl-copy/xclip)."
    if handoff.url:
        if _open_url(handoff.url):
            return True, handoff.note
        return False, "Could not open the Cursor link."
    try:
        result = subprocess.run(handoff.command, input=handoff.stdin, text=True, cwd=handoff.cwd, check=False)
    except OSError as exc:
        return False, f"Could not start {handoff.command[0]}: {exc}"
    if result.returncode != 0:
        return False, f"{Path(handoff.command[0]).name} exited with code {result.returncode}."
    return True, handoff.note
