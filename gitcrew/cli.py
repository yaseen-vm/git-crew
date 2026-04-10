"""
cli.py — Typer CLI entry point

Commands:
  git-crew review              Review staged changes (git diff --cached)
  git-crew review HEAD~3..HEAD Review a commit range
  git-crew review --unstaged   Review working-tree changes (not yet staged)
  git-crew pr 42               Review a GitHub PR by number
  git-crew install-hook        Install as a git pre-push hook
  git-crew uninstall-hook      Remove the git hook

All commands accept:
  --output FILE     Save Markdown report to a file
  --no-interactive  Skip the AutoGen Q&A session after the report

Environment variables required (put in .env or export before running):
  GROQ_API_KEY   — used by all LLM calls (LangGraph classify, all CrewAI crews, AutoGen)
"""

import sys
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console

load_dotenv()  # load .env if present

from .git import (
    get_staged_diff,
    get_working_tree_diff,
    get_commit_range_diff,
    get_pr_diff,
    parse_diff,
)
from .orchestrator import stream_review
from .report import (
    print_header,
    print_step,
    print_skip_notice,
    print_report,
    save_report,
    post_pr_comment,
    print_error,
)
from .interactive import start_interactive_session

app = typer.Typer(
    name="git-crew",
    help="AI-powered git diff reviewer — LangGraph + CrewAI + AutoGen",
    add_completion=False,
)
console = Console()

_HOOK_SCRIPT = """\
#!/bin/sh
# git-crew pre-push hook — installed by `git-crew install-hook`
git-crew review --no-interactive
if [ $? -ne 0 ]; then
  echo ""
  echo "git-crew: Review failed or found CRITICAL issues. Push blocked."
  echo "         Run `git-crew review` for details, or use --no-verify to bypass."
  exit 1
fi
"""


# ── review command ────────────────────────────────────────────────────────────

@app.command()
def review(
    commit_range: Optional[str] = typer.Argument(
        None,
        help="Git range to review, e.g. HEAD~3..HEAD or main..feature. "
             "Defaults to staged changes (git diff --cached).",
    ),
    unstaged: bool = typer.Option(
        False, "--unstaged",
        help="Review unstaged working-tree changes instead of staged changes.",
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o",
        help="Save the Markdown report to this file.",
    ),
    no_interactive: bool = typer.Option(
        False, "--no-interactive",
        help="Skip the AutoGen Q&A session after the report.",
    ),
    repo_path: str = typer.Option(
        ".", "--repo", "-r",
        help="Path to the git repository root.",
    ),
):
    """
    Review staged changes, a commit range, or unstaged working-tree changes.

    Examples:
      git-crew review                    # staged changes
      git-crew review HEAD~3..HEAD       # last 3 commits
      git-crew review main..feature      # branch diff
      git-crew review --unstaged         # working-tree changes
      git-crew review -o report.md       # save report
      git-crew review --no-interactive   # non-interactive (CI/hooks)
    """
    # ── Fetch diff ────────────────────────────────────────────────────────────
    try:
        if commit_range:
            diff_text = get_commit_range_diff(commit_range, repo_path=repo_path)
            source_label = commit_range
        elif unstaged:
            diff_text = get_working_tree_diff(repo_path=repo_path)
            source_label = "working tree (unstaged)"
        else:
            diff_text = get_staged_diff(repo_path=repo_path)
            source_label = "staged changes"
    except RuntimeError as e:
        print_error(str(e))
        raise typer.Exit(1)

    _run_review_pipeline(
        diff_text=diff_text,
        source_label=source_label,
        repo_path=repo_path,
        pr_number=None,
        output=output,
        no_interactive=no_interactive,
    )


# ── pr command ────────────────────────────────────────────────────────────────

@app.command()
def pr(
    pr_number: int = typer.Argument(..., help="GitHub PR number to review."),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o",
        help="Save the Markdown report to this file.",
    ),
    post_comment: bool = typer.Option(
        False, "--post-comment",
        help="Post the report as a PR comment via `gh`.",
    ),
    no_interactive: bool = typer.Option(
        False, "--no-interactive",
        help="Skip the AutoGen Q&A session.",
    ),
):
    """
    Review a GitHub PR by number.

    Requires the `gh` CLI to be installed and authenticated.

    Examples:
      git-crew pr 42
      git-crew pr 42 --post-comment     # posts report as PR comment
      git-crew pr 42 -o review.md       # saves report to file
    """
    try:
        diff_text = get_pr_diff(pr_number)
    except RuntimeError as e:
        print_error(str(e))
        raise typer.Exit(1)

    final_state = _run_review_pipeline(
        diff_text=diff_text,
        source_label=f"PR #{pr_number}",
        repo_path=".",
        pr_number=pr_number,
        output=output,
        no_interactive=no_interactive,
    )

    if post_comment and final_state:
        post_pr_comment(pr_number, final_state["final_report"])


# ── install-hook command ──────────────────────────────────────────────────────

@app.command(name="install-hook")
def install_hook(
    repo_path: str = typer.Option(".", "--repo", "-r", help="Path to the git repo root."),
):
    """
    Install git-crew as a pre-push hook.

    The hook runs `git-crew review --no-interactive` before every push.
    Exit code 1 (from CRITICAL findings or errors) blocks the push.

    To bypass: git push --no-verify
    """
    hook_path = Path(repo_path) / ".git" / "hooks" / "pre-push"
    if hook_path.exists():
        overwrite = typer.confirm(f"{hook_path} already exists. Overwrite?")
        if not overwrite:
            raise typer.Exit(0)

    hook_path.write_text(_HOOK_SCRIPT, encoding="utf-8")
    hook_path.chmod(0o755)
    console.print(f"[green]✓ Hook installed →[/green] {hook_path}")
    console.print("[dim]To bypass: git push --no-verify[/dim]")


@app.command(name="uninstall-hook")
def uninstall_hook(
    repo_path: str = typer.Option(".", "--repo", "-r", help="Path to the git repo root."),
):
    """Remove the git-crew pre-push hook."""
    hook_path = Path(repo_path) / ".git" / "hooks" / "pre-push"
    if not hook_path.exists():
        console.print("[yellow]No pre-push hook found.[/yellow]")
        raise typer.Exit(0)

    hook_path.unlink()
    console.print(f"[green]✓ Hook removed →[/green] {hook_path}")


# ── Internal pipeline runner ──────────────────────────────────────────────────

def _run_review_pipeline(
    diff_text: str,
    source_label: str,
    repo_path: str,
    pr_number: Optional[int],
    output: Optional[Path],
    no_interactive: bool,
) -> Optional[dict]:
    """
    Shared logic for review and pr commands:
      1. Validate diff is non-empty
      2. Print header
      3. Stream the LangGraph pipeline with live progress ticks
      4. Print the final report
      5. Optionally save to file
      6. Optionally start AutoGen interactive session
    """
    if not diff_text.strip():
        console.print("[yellow]No changes found in diff — nothing to review.[/yellow]")
        raise typer.Exit(0)

    # Rough file count from parsing (for header display)
    hunks = parse_diff(diff_text)
    file_count = len({h.file_path for h in hunks})
    print_header(source_label, file_count)

    # ── Stream the pipeline ───────────────────────────────────────────────────
    final_state: dict = {}
    try:
        for node_name, node_state in stream_review(
            diff_text=diff_text,
            repo_path=repo_path,
            pr_number=pr_number,
        ):
            print_step(node_name)
            final_state.update(node_state)
    except Exception as e:
        print_error(f"Review pipeline failed: {e}")
        raise typer.Exit(1)

    # ── Output ────────────────────────────────────────────────────────────────
    print_skip_notice(final_state.get("is_docs_only", False))
    print_report(final_state.get("final_report", "_No report generated._"))

    if output:
        save_report(final_state.get("final_report", ""), output)

    # ── Interactive session ───────────────────────────────────────────────────
    if not no_interactive and sys.stdin.isatty():
        want_session = typer.confirm(
            "\nStart interactive Q&A session to ask questions about the findings?",
            default=True,
        )
        if want_session:
            start_interactive_session(
                final_report=final_state.get("final_report", ""),
                diff_text=diff_text,
            )

    return final_state


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    app()


if __name__ == "__main__":
    main()
