"""
orchestrator.py — LangGraph review pipeline

This module owns the top-level review workflow. It is the only place where
LangGraph is used, and it earns that choice by doing what LangGraph is
actually good at: stateful, conditional, multi-step pipelines.

Graph topology:
  START → classify → security_review → architecture_review → performance_review → aggregate → END

Each node receives the full ReviewState dict, mutates a subset of its keys,
and returns the delta. LangGraph merges the delta back into state automatically.

Key design decisions:
  - classify() sets boolean flags (run_security, run_architecture, run_performance).
    The review nodes check their own flag and return empty strings if False.
    This keeps the graph topology simple (always linear) while still being
    intelligent about what to skip.

  - The diff_text stored in state is already formatted (format_hunks_for_review).
    Raw hunks are never stored in state because TypedDict values must be
    JSON-serialisable for LangGraph's checkpointing to work.

  - Each crew call is wrapped in a try/except so one crew failure doesn't
    abort the whole review.
"""

import os
from typing import Optional
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, END

from .git import diff_summary, parse_diff, format_hunks_for_review, DiffHunk
from .crews.security_crew import run_security_crew
from .crews.architecture_crew import run_architecture_crew
from .crews.performance_crew import run_performance_crew


# ── State schema ──────────────────────────────────────────────────────────────

class ReviewState(TypedDict):
    # ── Input (set before graph.invoke) ─────────────────────────────────────
    diff_text: str            # raw `git diff` output
    repo_path: str            # path to the repository root (for context messages)
    pr_number: Optional[int]  # PR number if reviewing a PR, else None

    # ── Set by classify node ─────────────────────────────────────────────────
    formatted_diff: str       # format_hunks_for_review() output — sent to crews
    files_changed: list       # list of changed file paths
    languages: list           # list of detected languages
    has_security_files: bool  # True if any path hits the security keyword list
    is_docs_only: bool        # True if only .md/.txt/.rst files changed
    run_security: bool        # whether to run the Security Crew
    run_architecture: bool    # whether to run the Architecture Crew
    run_performance: bool     # whether to run the Performance Crew

    # ── Set by review nodes ──────────────────────────────────────────────────
    security_findings: str
    architecture_findings: str
    performance_findings: str

    # ── Set by aggregate node ────────────────────────────────────────────────
    final_report: str


# ── Nodes ─────────────────────────────────────────────────────────────────────

_DOCS_LANGS = {"Markdown", "Unknown"}


def classify(state: ReviewState) -> dict:
    """
    Parse the raw diff and decide which specialist crews to run.

    Routing logic:
      - docs-only diff       → skip all crews (nothing to review)
      - security-sensitive files → always run security crew
      - non-trivial code diff → run all three crews
      - trivial diff (<5 lines added) → still run all, crews will find little

    Sets: formatted_diff, files_changed, languages, has_security_files,
          is_docs_only, run_security, run_architecture, run_performance
    """
    diff_text = state["diff_text"]

    hunks: list[DiffHunk] = parse_diff(diff_text)
    summary = diff_summary(hunks)
    formatted = format_hunks_for_review(hunks)

    is_docs_only: bool = summary["is_docs_only"]
    has_security: bool = summary["has_security_files"]

    # Always run all crews on real code; skip only for pure doc changes
    run_security = not is_docs_only
    run_architecture = not is_docs_only
    run_performance = not is_docs_only

    # Force security crew if security-sensitive files changed, even if borderline
    if has_security:
        run_security = True

    return {
        "formatted_diff": formatted,
        "files_changed": summary["files"],
        "languages": summary["languages"],
        "has_security_files": has_security,
        "is_docs_only": is_docs_only,
        "run_security": run_security,
        "run_architecture": run_architecture,
        "run_performance": run_performance,
    }


def security_review(state: ReviewState) -> dict:
    """Call the CrewAI Security Crew. Skipped if run_security is False."""
    if not state["run_security"]:
        return {"security_findings": ""}

    try:
        findings = run_security_crew(state["formatted_diff"])
    except Exception as e:
        findings = f"⚠️ Security crew failed: {e}"

    return {"security_findings": findings}


def architecture_review(state: ReviewState) -> dict:
    """Call the CrewAI Architecture Crew. Skipped if run_architecture is False."""
    if not state["run_architecture"]:
        return {"architecture_findings": ""}

    try:
        findings = run_architecture_crew(state["formatted_diff"])
    except Exception as e:
        findings = f"⚠️ Architecture crew failed: {e}"

    return {"architecture_findings": findings}


def performance_review(state: ReviewState) -> dict:
    """Call the CrewAI Performance Crew. Skipped if run_performance is False."""
    if not state["run_performance"]:
        return {"performance_findings": ""}

    try:
        findings = run_performance_crew(state["formatted_diff"])
    except Exception as e:
        findings = f"⚠️ Performance crew failed: {e}"

    return {"performance_findings": findings}


def aggregate(state: ReviewState) -> dict:
    """
    Assemble the final Markdown report from all crew findings.

    Structure:
      # Git-Crew Review Report
      ## Summary table
      ## 🔒 Security Findings
      ## 🏗️ Architecture Findings
      ## ⚡ Performance Findings
      ## What Changed (file list)
    """
    pr_label = f"PR #{state['pr_number']}" if state.get("pr_number") else "local diff"
    langs = ", ".join(state["languages"]) if state["languages"] else "Unknown"
    files = state["files_changed"]

    # Count issues by scanning for severity labels in findings
    def count_label(text: str, label: str) -> int:
        return text.upper().count(f"[{label}]") + text.upper().count(f"**{label}**")

    all_findings = (
        state["security_findings"]
        + state["architecture_findings"]
        + state["performance_findings"]
    )
    critical = count_label(all_findings, "CRITICAL")
    high = count_label(all_findings, "HIGH")
    medium = count_label(all_findings, "MEDIUM")
    low = count_label(all_findings, "LOW")

    files_list = "\n".join(f"- `{f}`" for f in files) or "_No files detected._"

    security_section = (
        state["security_findings"]
        if state["run_security"]
        else "_Security review skipped (docs-only diff)._"
    )
    architecture_section = (
        state["architecture_findings"]
        if state["run_architecture"]
        else "_Architecture review skipped (docs-only diff)._"
    )
    performance_section = (
        state["performance_findings"]
        if state["run_performance"]
        else "_Performance review skipped (docs-only diff)._"
    )

    report = f"""# Git-Crew Review Report

**Source:** {pr_label}
**Languages:** {langs}
**Files reviewed:** {len(files)}

| Severity | Count |
|---|---|
| 🔴 CRITICAL | {critical} |
| 🟠 HIGH | {high} |
| 🟡 MEDIUM | {medium} |
| 🟢 LOW | {low} |

---

## 🔒 Security Findings

{security_section}

---

## 🏗️ Architecture Findings

{architecture_section}

---

## ⚡ Performance Findings

{performance_section}

---

## 📁 Files Changed

{files_list}
"""

    return {"final_report": report.strip()}


# ── Graph construction ────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """
    Build and compile the LangGraph review pipeline.

    Returns a compiled graph ready for .invoke() or .stream().
    The graph is built once at module import and reused for all reviews.
    """
    g = StateGraph(ReviewState)

    g.add_node("classify",             classify)
    g.add_node("security_review",      security_review)
    g.add_node("architecture_review",  architecture_review)
    g.add_node("performance_review",   performance_review)
    g.add_node("aggregate",            aggregate)

    g.set_entry_point("classify")
    g.add_edge("classify",            "security_review")
    g.add_edge("security_review",     "architecture_review")
    g.add_edge("architecture_review", "performance_review")
    g.add_edge("performance_review",  "aggregate")
    g.add_edge("aggregate",           END)

    return g.compile()


# Build once at import — reused by cli.py
_graph = build_graph()


def run_review(diff_text: str, repo_path: str = ".", pr_number: int | None = None) -> ReviewState:
    """
    Run the full review pipeline on a diff string.

    Args:
        diff_text:   Raw output of `git diff` or `gh pr diff`
        repo_path:   Root of the repository (used for context display)
        pr_number:   PR number if reviewing a GitHub PR

    Returns:
        Final ReviewState with all findings and the assembled final_report.

    Usage:
        state = run_review(diff_text)
        print(state["final_report"])
    """
    initial: dict = {
        "diff_text": diff_text,
        "repo_path": repo_path,
        "pr_number": pr_number,
        # ── defaults for all other fields ──
        "formatted_diff": "",
        "files_changed": [],
        "languages": [],
        "has_security_files": False,
        "is_docs_only": False,
        "run_security": False,
        "run_architecture": False,
        "run_performance": False,
        "security_findings": "",
        "architecture_findings": "",
        "performance_findings": "",
        "final_report": "",
    }
    return _graph.invoke(initial)


def stream_review(diff_text: str, repo_path: str = ".", pr_number: int | None = None):
    """
    Stream node completions for live progress display in the CLI.

    Yields:
        (node_name: str, updated_state: dict) after each node completes.

    Usage:
        for node_name, state in stream_review(diff_text):
            print(f"✓ {node_name}")
    """
    initial: dict = {
        "diff_text": diff_text,
        "repo_path": repo_path,
        "pr_number": pr_number,
        "formatted_diff": "",
        "files_changed": [],
        "languages": [],
        "has_security_files": False,
        "is_docs_only": False,
        "run_security": False,
        "run_architecture": False,
        "run_performance": False,
        "security_findings": "",
        "architecture_findings": "",
        "performance_findings": "",
        "final_report": "",
    }
    for event in _graph.stream(initial):
        node_name = next(iter(event))
        yield node_name, event[node_name]
