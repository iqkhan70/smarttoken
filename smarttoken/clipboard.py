"""Copy pack markdown to the system clipboard when --copy is set."""

from __future__ import annotations

import shutil
import subprocess


def copy_text(text: str) -> bool:
    for command in _candidates():
        try:
            result = subprocess.run(command, input=text, check=False, text=True, capture_output=True)
        except OSError:
            continue
        if result.returncode == 0:
            return True
    return False


def _candidates() -> list[list[str]]:
    commands: list[list[str]] = []
    if shutil.which("pbcopy"):
        commands.append(["pbcopy"])
    if shutil.which("wl-copy"):
        commands.append(["wl-copy"])
    if shutil.which("xclip"):
        commands.append(["xclip", "-selection", "clipboard"])
    if shutil.which("xsel"):
        commands.append(["xsel", "--clipboard", "--input"])
    return commands
