"""
report.py — Output rendering and delivery

Handles all output channels after the review is complete:
  - Rich terminal rendering (always)
  - Optional Markdown file save (--output flag)
  - Optional SARIF 2.1.0 file save (--sarif flag) for GitHub Code Scanning
  - Optional GitHub PR comment posting (via `gh` CLI)

No LLM calls are made here. This module is pure formatting + subprocess.
"""

import json
import subprocess
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

console = Console()

_STEP_LABELS = {
    "classify":      "Classifying diff",
    "run_all_crews": "Running security · architecture · performance crews (parallel)",
    "aggregate":     "Building report",
    # Legacy node names kept for compatibility if someone uses run_review directly
    "security_review":     "Security crew",
    "architecture_review": "Architecture crew",
    "performance_review":  "Performance crew",
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
    from .llm import describe_active
    console.print()
    header = Text()
    header.append("git-crew", style="bold cyan")
    header.append(f"  reviewing {source_label}", style="dim")
    header.append(f"  ({file_count} files)  ", style="dim")
    header.append(f"[{describe_active()}]", style="dim yellow")
    console.print(Panel(header, border_style="cyan", padding=(0, 1)))
    console.print()


def save_report(final_report: str, output_path: Path) -> None:
    """Write the Markdown report to a file."""
    output_path.write_text(final_report, encoding="utf-8")
    console.print(f"\n[green]Report saved →[/green] {output_path}")


# ── SARIF output ──────────────────────────────────────────────────────────────

_SARIF_RULES = [
    {
        "id": "GC001",
        "name": "SecurityFinding",
        "shortDescription": {"text": "Security vulnerability detected in diff"},
        "helpUri": "https://github.com/yaseen-vm/git-crew",
    },
    {
        "id": "GC002",
        "name": "ArchitectureFinding",
        "shortDescription": {"text": "Architecture or code quality issue detected in diff"},
        "helpUri": "https://github.com/yaseen-vm/git-crew",
    },
    {
        "id": "GC003",
        "name": "PerformanceFinding",
        "shortDescription": {"text": "Performance bottleneck detected in diff"},
        "helpUri": "https://github.com/yaseen-vm/git-crew",
    },
]

_CREW_RULE_MAP = {
    "security_findings":     ("GC001", "Security"),
    "architecture_findings": ("GC002", "Architecture"),
    "performance_findings":  ("GC003", "Performance"),
}


def _findings_to_sarif_level(text: str) -> str:
    """Map the highest severity label in a findings block to a SARIF level."""
    up = text.upper()
    if "CRITICAL" in up or "[HIGH]" in up or "**HIGH**" in up:
        return "error"
    if "MEDIUM" in up or "**MEDIUM**" in up:
        return "warning"
    return "note"


def save_sarif(state: dict, output_path: Path) -> None:
    """
    Write a SARIF 2.1.0 file from the review state.

    SARIF is the standard format for GitHub Code Scanning. Upload with:
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: results.sarif

    Findings appear as inline annotations in the Files Changed tab and
    in the repository's Security → Code scanning page.

    Args:
        state:        Final ReviewState dict (must contain *_findings keys)
        output_path:  Destination .sarif file path
    """
    results = []

    for state_key, (rule_id, label) in _CREW_RULE_MAP.items():
        text = state.get(state_key, "")
        if not text or text.startswith("_") or text.startswith("⚠️"):
            continue

        results.append({
            "ruleId": rule_id,
            "level": _findings_to_sarif_level(text),
            "message": {
                "text": f"{label} review findings:\n\n{text[:2000]}"
            },
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {
                        "uri": ".",
                        "uriBaseId": "%SRCROOT%",
                    }
                }
            }],
        })

    sarif_doc = {
        "$schema": (
            "https://raw.githubusercontent.com/oasis-tcs/sarif-spec"
            "/master/Schemata/sarif-schema-2.1.0.json"
        ),
        "version": "2.1.0",
        "runs": [{
            "tool": {
                "driver": {
                    "name": "git-crew",
                    "informationUri": "https://github.com/yaseen-vm/git-crew",
                    "version": "0.1.0",
                    "rules": _SARIF_RULES,
                }
            },
            "results": results,
        }],
    }

    output_path.write_text(json.dumps(sarif_doc, indent=2), encoding="utf-8")
    console.print(f"\n[green]SARIF saved →[/green] {output_path}")


# ── GitHub PR comment ─────────────────────────────────────────────────────────

def post_pr_comment(pr_number: int, final_report: str) -> None:
    """
    Post the report as a PR review comment using the GitHub CLI.

    Requires:
      - `gh` installed (https://cli.github.com)
      - GH_TOKEN set, or `gh auth login` completed
    """
    result = subprocess.run(
        ["gh", "pr", "comment", str(pr_number), "--body", final_report],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(
            f"[red]Failed to post PR comment:[/red] {result.stderr.strip()}\n"
            "Ensure `gh` is installed and GH_TOKEN is set (or run `gh auth login`)."
        )
    else:
        console.print(f"\n[green]Comment posted →[/green] PR #{pr_number}")


def print_error(message: str) -> None:
    """Print a formatted error message."""
    console.print(f"\n[red]Error:[/red] {message}")
