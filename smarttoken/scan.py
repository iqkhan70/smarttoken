"""Walk a local repo, honor gitignore, skip binaries and huge files."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pathspec

from smarttoken.config import Settings
from smarttoken.models import ScannedFile

ALWAYS_SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "target",
    "vendor",
    ".idea",
    ".smarttoken",
}

BINARY_EXTENSIONS = {
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
    ".woff",
    ".woff2",
    ".class",
    ".pyc",
    ".pyo",
    ".wasm",
    ".jar",
    ".bin",
    ".dat",
}

LANG_BY_EXT = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".md": "markdown",
    ".rst": "rst",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".sql": "sql",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".tf": "terraform",
    ".swift": "swift",
    ".scala": "scala",
}

LOCKFILE_NAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "cargo.lock",
    "composer.lock",
    "gemfile.lock",
}

ENTRYPOINT_NAMES = {
    "main.py",
    "app.py",
    "wsgi.py",
    "asgi.py",
    "manage.py",
    "__main__.py",
    "index.ts",
    "index.js",
    "main.ts",
    "main.go",
    "main.rs",
    "main.java",
}

ENTRYPOINT_DIRS = {"src", "cmd", "app", "lib"}


def infer_lang(rel_path: str) -> str:
    ext = Path(rel_path).suffix.lower()
    return LANG_BY_EXT.get(ext, "other")


def infer_role(rel_path: str) -> str:
    path = rel_path.replace("\\", "/").lower()
    name = Path(path).name
    parts = path.split("/")

    if name in LOCKFILE_NAMES or name.endswith((".min.js", ".min.css")):
        return "generated"
    if any(part in {"dist", "vendor", "generated", "node_modules", "build"} for part in parts):
        return "generated"
    if (
        any(part in {"test", "tests", "spec", "__tests__"} for part in parts)
        or name.startswith("test_")
        or name.endswith(("_test.py", ".spec.ts", ".spec.js", ".test.ts", ".test.js"))
        or ".spec." in name
        or ".test." in name
    ):
        return "test"
    if name in {"readme.md", "contributing.md", "changelog.md", "architecture.md"} or path.startswith(
        "docs/"
    ):
        return "doc"
    if name.endswith(".md"):
        return "doc"
    if (
        any(part in {"deploy", "scripts"} for part in parts)
        or name.endswith((".sh", ".sql", ".csproj"))
    ):
        return "config"
    if name in {
        "package.json",
        "pyproject.toml",
        "cargo.toml",
        "go.mod",
        "dockerfile",
        "makefile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "tsconfig.json",
    } or name.endswith((".yml", ".yaml", ".toml", ".ini", ".cfg", ".env.example")):
        return "config"
    return "source"


def is_entrypoint(rel_path: str) -> bool:
    path = rel_path.replace("\\", "/")
    name = Path(path).name.lower()
    if name in ENTRYPOINT_NAMES:
        return True
    parts = path.split("/")
    if len(parts) <= 2 and parts[0] in ENTRYPOINT_DIRS:
        return True
    return False


def git_metadata(root: Path) -> tuple[str | None, str | None]:
    remote = _git(root, ["remote", "get-url", "origin"])
    branch = _git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    return remote, branch


def is_git_repo(root: Path) -> bool:
    return (root / ".git").exists() and (
        (root / ".git").is_dir() or (root / ".git").is_file()
    )


def _git(root: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _git_ls_files(root: Path) -> list[str] | None:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            check=False,
            capture_output=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    raw = result.stdout.split(b"\0")
    return [item.decode("utf-8", errors="replace") for item in raw if item]


def _load_ignore(root: Path) -> pathspec.PathSpec:
    patterns = [f"{name}/" for name in sorted(ALWAYS_SKIP_DIRS)]
    gitignore = root / ".gitignore"
    if gitignore.is_file():
        patterns.extend(gitignore.read_text(encoding="utf-8", errors="replace").splitlines())
    return pathspec.PathSpec.from_lines("gitignore", patterns)


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def iter_rel_paths(root: Path) -> list[str]:
    if is_git_repo(root):
        listed = _git_ls_files(root)
        if listed is not None:
            return [p for p in listed if p and not p.endswith("/")]

    spec = _load_ignore(root)
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        dirnames[:] = [
            name
            for name in dirnames
            if name not in ALWAYS_SKIP_DIRS
            and not spec.match_file(
                str(Path(rel_dir, name).as_posix()) + "/"
                if rel_dir != "."
                else name + "/"
            )
        ]
        for name in filenames:
            rel = name if rel_dir == "." else str(Path(rel_dir, name).as_posix())
            if spec.match_file(rel):
                continue
            found.append(rel)
    return found


def scan_repo(root: Path, settings: Settings) -> tuple[list[ScannedFile], int]:
    """Return (indexable files, skipped count)."""
    scanned: list[ScannedFile] = []
    skipped = 0
    for rel in iter_rel_paths(root):
        abs_path = root / rel
        if not abs_path.is_file():
            skipped += 1
            continue
        suffix = abs_path.suffix.lower()
        if suffix in BINARY_EXTENSIONS:
            skipped += 1
            continue
        try:
            size = abs_path.stat().st_size
        except OSError:
            skipped += 1
            continue
        if size > settings.max_file_bytes:
            skipped += 1
            continue
        try:
            data = abs_path.read_bytes()
        except OSError:
            skipped += 1
            continue
        if _looks_binary(data):
            skipped += 1
            continue
        text = data.decode("utf-8", errors="replace")
        scanned.append(
            ScannedFile(
                rel_path=rel.replace("\\", "/"),
                abs_path=str(abs_path),
                lang=infer_lang(rel),
                role=infer_role(rel),
                hash=_hash_bytes(data),
                mtime=abs_path.stat().st_mtime,
                size=size,
                content=text,
            )
        )
    return scanned, skipped
