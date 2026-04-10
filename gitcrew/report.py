"""
report.py — Output rendering and delivery

Handles all output channels after the review is complete:
  - Rich terminal rendering (always)
  - Optional Markdown file save (--output flag)
  - Optional GitHub PR comment posting (via `gh` CLI)

No LLM calls are made here. This module is pure formatting + subprocess.
"""

import subprocess
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

console = Console()

_STEP_LABELS = {
    "classify":            "Classifying diff",
    "security_review":     "Security crew running",
    "architecture_review": "Architecture crew running",
    "performance_review":  "Performance crew running",
    "aggregate":           "Building report",
}


def print_step(node_name: str) -> None:
    """Print a progress tick as each LangGraph node completes."""
    label = _STEP_LABELS.get(node_name, node_name)
    console.print(f"  [green]✓[/green] {label}")


def print_skip_notice(is_docs_only: bool) -> None:
    """Inform the developer why crews were skipped."""
    if is_docs_only:
        console.print(
            Panel(
                "[yellow]Only documentation files changed — "
                "security, architecture, and performance crews were skipped.[/yellow]",
                title="Skip Notice",
                border_style="yellow",
            )
        )


def print_report(final_report: str) -> None:
    """Render the assembled Markdown report in the terminal using Rich."""
    console.print()
    console.print(Markdown(final_report))


def print_header(source_label: str, file_count: int) -> None:
    """Print the review header before progress ticks start."""
    console.print()
    header = Text()
    header.append("git-crew", style="bold cyan")
    header.append(f"  reviewing {source_label}", style="dim")
    header.append(f"  ({file_count} source hint)", style="dim")
    console.print(Panel(header, border_style="cyan", padding=(0, 1)))
    console.print()


def save_report(final_report: str, output_path: Path) -> None:
    """
    Write the Markdown report to a file.

    Args:
        final_report:  The assembled Markdown string from the aggregate node
        output_path:   Destination file path
    """
    output_path.write_text(final_report, encoding="utf-8")
    console.print(f"\n[green]Report saved →[/green] {output_path}")


def post_pr_comment(pr_number: int, final_report: str) -> None:
    """
    Post the report as a PR review comment using the GitHub CLI.

    Requires:
      - `gh` installed (https://cli.github.com)
      - `gh auth login` completed
      - The current directory is inside the target repository

    Args:
        pr_number:     GitHub PR number
        final_report:  Markdown report to post
    """
    result = subprocess.run(
        ["gh", "pr", "comment", str(pr_number), "--body", final_report],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(
            f"[red]Failed to post PR comment:[/red] {result.stderr.strip()}\n"
            "Ensure `gh` is installed and you have run `gh auth login`."
        )
    else:
        console.print(f"\n[green]Comment posted →[/green] PR #{pr_number}")


def print_error(message: str) -> None:
    """Print a formatted error message and exit hint."""
    console.print(f"\n[red]Error:[/red] {message}")
