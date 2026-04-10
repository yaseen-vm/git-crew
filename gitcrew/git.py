"""
git.py — Diff parsing and git/GitHub integration.

Responsibilities:
  - DiffHunk dataclass: structured representation of one changed code block
  - parse_diff(text) → list[DiffHunk]: turn raw `git diff` text into objects
  - get_staged_diff / get_commit_range_diff / get_pr_diff: fetch diffs from git/gh
  - format_hunks_for_review: render hunks as LLM-ready Markdown text
  - diff_summary: extract metadata (languages, files, security sensitivity)

No LLM calls are made here. This module is pure parsing + subprocess.
"""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


# ── DiffHunk dataclass ────────────────────────────────────────────────────────

@dataclass
class DiffHunk:
    """
    One contiguous block of changed lines within a single file.

    Attributes:
        file_path           Relative path to the changed file, e.g. "src/auth.py"
        language            Display name inferred from extension, e.g. "Python"
        start_line          First line number of this hunk in the *new* file
        hunk_header         The @@ line, e.g. "@@ -45,8 +47,12 @@ def login"
        raw                 Full hunk text including context lines (space-prefixed)
        added_lines         Lines that were added (leading + stripped)
        removed_lines       Lines that were removed (leading - stripped)
        is_security_file    True if the file path contains security-related keywords
    """
    file_path: str
    language: str
    start_line: int
    hunk_header: str
    raw: str
    added_lines: list[str]
    removed_lines: list[str]
    is_security_file: bool = False


# ── Language detection ────────────────────────────────────────────────────────

_EXT_LANG: dict[str, str] = {
    ".py": "Python",       ".js": "JavaScript",  ".ts": "TypeScript",
    ".tsx": "TypeScript",  ".jsx": "JavaScript",  ".java": "Java",
    ".go": "Go",           ".rs": "Rust",         ".cpp": "C++",
    ".cc": "C++",          ".cxx": "C++",         ".c": "C",
    ".cs": "C#",           ".rb": "Ruby",         ".php": "PHP",
    ".kt": "Kotlin",       ".swift": "Swift",     ".sh": "Shell",
    ".bash": "Shell",      ".yaml": "YAML",       ".yml": "YAML",
    ".json": "JSON",       ".md": "Markdown",     ".sql": "SQL",
    ".html": "HTML",       ".css": "CSS",         ".scss": "CSS",
    ".tf": "Terraform",    ".toml": "TOML",       ".ini": "INI",
}

_DOCS_ONLY_EXTS: frozenset[str] = frozenset({".md", ".txt", ".rst"})

_SECURITY_KEYWORDS: frozenset[str] = frozenset({
    "auth", "login", "logout", "password", "passwd", "secret",
    "token", "credential", "crypto", "encrypt", "decrypt",
    "ssl", "tls", "cert", "key", "permission", "access",
    "session", "cookie", "jwt", "oauth", "saml", "iam",
    "rbac", "acl", "firewall", "sanitize", "validate", "hash",
    "signature", "middleware", "guard",
})


def infer_language(file_path: str) -> str:
    return _EXT_LANG.get(Path(file_path).suffix.lower(), "Unknown")


def _is_security_file(file_path: str) -> bool:
    normalized = file_path.lower().replace("\\", "/")
    parts = normalized.replace(".", "/").split("/")
    return any(kw in part for kw in _SECURITY_KEYWORDS for part in parts)


# ── Diff parser ───────────────────────────────────────────────────────────────

def parse_diff(diff_text: str) -> list[DiffHunk]:
    """
    Parse raw `git diff` output into a list of DiffHunk objects.

    Handles:
      - Multiple files in one diff
      - Multiple hunks per file
      - Binary files (skipped — no added/removed lines)
      - New files, deleted files, renames

    Example input:
        diff --git a/src/auth.py b/src/auth.py
        index 1234567..abcdefg 100644
        --- a/src/auth.py
        +++ b/src/auth.py
        @@ -45,6 +45,8 @@ def login(user, password):
        -    query = f"SELECT * FROM users WHERE user='{user}'"
        +    query = "SELECT * FROM users WHERE user=?"
        +    cursor.execute(query, (user,))
    """
    hunks: list[DiffHunk] = []
    current_file: str | None = None
    current_lang = "Unknown"
    current_security = False

    lines = diff_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        # ── New file entry ────────────────────────────────────────────────
        if line.startswith("diff --git "):
            current_file = None
            i += 1
            continue

        # +++ b/path/to/file  → extract the new-file path
        if line.startswith("+++ "):
            raw_path = line[4:]
            path = raw_path[2:] if raw_path.startswith("b/") else raw_path
            if path not in ("/dev/null", "dev/null"):
                current_file = path
                current_lang = infer_language(path)
                current_security = _is_security_file(path)
            i += 1
            continue

        # ── Hunk header ───────────────────────────────────────────────────
        if line.startswith("@@") and current_file:
            hunk_header = line
            m = re.search(r"\+(\d+)", line)
            start_line = int(m.group(1)) if m else 0

            hunk_lines = [line]
            added: list[str] = []
            removed: list[str] = []
            i += 1

            while i < len(lines):
                l = lines[i]
                if l.startswith("@@") or l.startswith("diff --git"):
                    break
                hunk_lines.append(l)
                if l.startswith("+") and not l.startswith("+++"):
                    added.append(l[1:])
                elif l.startswith("-") and not l.startswith("---"):
                    removed.append(l[1:])
                i += 1

            if added or removed:
                hunks.append(DiffHunk(
                    file_path=current_file,
                    language=current_lang,
                    start_line=start_line,
                    hunk_header=hunk_header,
                    raw="\n".join(hunk_lines),
                    added_lines=added,
                    removed_lines=removed,
                    is_security_file=current_security,
                ))
            continue

        i += 1

    return hunks


# ── Git commands ──────────────────────────────────────────────────────────────

def _run_git(*args: str, cwd: str = ".") -> str:
    result = subprocess.run(
        ["git", *args],
        capture_output=True, text=True, cwd=cwd,
    )
    if result.returncode != 0:
        raise RuntimeError(f"`git {' '.join(args)}` failed:\n{result.stderr.strip()}")
    return result.stdout


def get_staged_diff(repo_path: str = ".") -> str:
    """Diff of staged (cached) changes — what would be committed right now."""
    return _run_git("diff", "--cached", "-U3", cwd=repo_path)


def get_working_tree_diff(repo_path: str = ".") -> str:
    """Diff of unstaged working-tree changes."""
    return _run_git("diff", "-U3", cwd=repo_path)


def get_commit_range_diff(commit_range: str, repo_path: str = ".") -> str:
    """
    Diff for any git range expression.

    Examples:
        "HEAD~3..HEAD"   last 3 commits
        "main..feature"  branch diff against main
        "abc123..def456" between two specific SHAs
    """
    return _run_git("diff", "-U3", commit_range, cwd=repo_path)


def get_pr_diff(pr_number: int) -> str:
    """
    Fetch a GitHub PR diff via the `gh` CLI.

    Requires:
        - `gh` installed (https://cli.github.com)
        - `gh auth login` completed
    """
    result = subprocess.run(
        ["gh", "pr", "diff", str(pr_number)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"`gh pr diff {pr_number}` failed:\n{result.stderr.strip()}\n"
            "Ensure `gh` is installed and you have run `gh auth login`."
        )
    return result.stdout


# ── Formatting ────────────────────────────────────────────────────────────────

def format_hunks_for_review(hunks: list[DiffHunk], max_chars: int = 14_000) -> str:
    """
    Render hunks as a Markdown string for inclusion in LLM prompts.

    Each hunk becomes a fenced code block annotated with file path and line number.
    Truncates at max_chars to avoid exceeding model context windows.

    The 14 000 char default is conservative for Groq's llama-3.3-70b context.
    Increase to ~50 000 for OpenAI gpt-4o or Claude models.
    """
    if not hunks:
        return "_No code changes detected._"

    parts: list[str] = []
    total = 0
    for idx, h in enumerate(hunks):
        block = (
            f"### `{h.file_path}` — line {h.start_line}"
            f"{' ⚠️ security-sensitive file' if h.is_security_file else ''}\n"
            f"```{h.language.lower()}\n{h.raw}\n```\n"
        )
        if total + len(block) > max_chars:
            remaining = len(hunks) - idx
            parts.append(
                f"\n> **[{remaining} more hunk(s) truncated — "
                f"review the full diff manually for complete coverage]**"
            )
            break
        parts.append(block)
        total += len(block)

    return "\n".join(parts)


def diff_summary(hunks: list[DiffHunk]) -> dict:
    """
    Return a metadata dictionary about the diff contents.
    Used by the LangGraph classify node to decide which crews to run.

    Returns:
        files           list of changed file paths (ordered, deduplicated)
        languages       list of languages detected (ordered, deduplicated)
        has_security_files   True if any file path hits the security keyword list
        is_docs_only    True if every changed file is .md / .txt / .rst
        total_added     total lines added across all hunks
        total_removed   total lines removed
        hunk_count      number of DiffHunk objects
    """
    files = list(dict.fromkeys(h.file_path for h in hunks))
    languages = list(dict.fromkeys(h.language for h in hunks))
    has_security_files = any(h.is_security_file for h in hunks)
    exts = {Path(f).suffix.lower() for f in files}
    is_docs_only = bool(exts) and exts.issubset(_DOCS_ONLY_EXTS)
    total_added = sum(len(h.added_lines) for h in hunks)
    total_removed = sum(len(h.removed_lines) for h in hunks)
    return {
        "files": files,
        "languages": languages,
        "has_security_files": has_security_files,
        "is_docs_only": is_docs_only,
        "total_added": total_added,
        "total_removed": total_removed,
        "hunk_count": len(hunks),
    }
