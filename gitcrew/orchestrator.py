"""
orchestrator.py — LangGraph review pipeline

This module owns the top-level review workflow. It is the only place where
LangGraph is used, and it earns that choice by doing what LangGraph is
actually good at: stateful, conditional, multi-step pipelines.

Graph topology:
  START → classify → run_all_crews → aggregate → END

Key design decisions:
  - classify() sets boolean flags (run_security, run_architecture, run_performance).

  - run_all_crews() runs all enabled crews in parallel using ThreadPoolExecutor,
    reducing total review time from 3× sequential to 1× max-crew-time.

  - The diff_text stored in state is already formatted (format_hunks_for_review).
    Raw hunks are never stored in state because TypedDict values must be
    JSON-serialisable for LangGraph's checkpointing to work.

  - Each crew call is wrapped in a try/except so one crew failure doesn't
    abort the whole review.
"""

from concurrent.futures import Future, ThreadPoolExecutor

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from .crews.architecture_crew import run_architecture_crew
from .crews.performance_crew import run_performance_crew
from .crews.security_crew import run_security_crew
from .git import DiffHunk, diff_summary, format_hunks_for_review, parse_diff


# ── State schema ──────────────────────────────────────────────────────────────

class ReviewState(TypedDict):
    # ── Input (set before graph.invoke) ─────────────────────────────────────
    diff_text: str            # raw `git diff` output
    repo_path: str            # path to the repository root (for context messages)
    pr_number: int | None  # PR number if reviewing a PR, else None

    # ── Set by classify node ─────────────────────────────────────────────────
    formatted_diff: str       # format_hunks_for_review() output — sent to crews
    files_changed: list       # list of changed file paths
    languages: list           # list of detected languages
    has_security_files: bool  # True if any path hits the security keyword list
    is_docs_only: bool        # True if only .md/.txt/.rst files changed
    run_security: bool        # whether to run the Security Crew
    run_architecture: bool    # whether to run the Architecture Crew
    run_performance: bool     # whether to run the Performance Crew

    # ── Set by run_all_crews node ────────────────────────────────────────────
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
      - docs-only diff            → skip all crews (nothing to review)
      - security-sensitive files  → always run security crew
      - any other code diff       → run all three crews
    """
    diff_text = state["diff_text"]

    hunks: list[DiffHunk] = parse_diff(diff_text)
    summary = diff_summary(hunks)
    formatted = format_hunks_for_review(hunks)

    is_docs_only: bool = summary["is_docs_only"]
    has_security: bool = summary["has_security_files"]

    run_security = not is_docs_only
    run_architecture = not is_docs_only
    run_performance = not is_docs_only

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


def run_all_crews(state: ReviewState) -> dict:
    """
    Run all enabled CrewAI crews in parallel using ThreadPoolExecutor.

    Sequential execution (old): total time ≈ t_security + t_architecture + t_performance
    Parallel execution (now):   total time ≈ max(t_security, t_architecture, t_performance)

    Each crew is wrapped in try/except — a single failure returns a warning string
    and does not abort the other crews or the overall review.
    """
    results = {
        "security_findings": "",
        "architecture_findings": "",
        "performance_findings": "",
    }

    if state["is_docs_only"]:
        return results

    formatted = state["formatted_diff"]

    crew_map: dict[str, tuple] = {}
    if state["run_security"]:
        crew_map["security"] = (run_security_crew, formatted)
    if state["run_architecture"]:
        crew_map["architecture"] = (run_architecture_crew, formatted)
    if state["run_performance"]:
        crew_map["performance"] = (run_performance_crew, formatted)

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures: dict[str, Future] = {
            key: executor.submit(fn, arg)
            for key, (fn, arg) in crew_map.items()
        }
        for key, future in futures.items():
            try:
                results[f"{key}_findings"] = future.result()
            except Exception as e:
                results[f"{key}_findings"] = f"⚠️ {key.title()} crew failed: {e}"

    return results


def aggregate(state: ReviewState) -> dict:
    """
    Assemble the final Markdown report from all crew findings.

    Structure:
      # Git-Crew Review Report
      ## Summary table (severity counts)
      ## 🔒 Security Findings
      ## 🏗️ Architecture Findings
      ## ⚡ Performance Findings
      ## 📁 Files Changed
    """
    pr_label = f"PR #{state['pr_number']}" if state.get("pr_number") else "local diff"
    langs = ", ".join(state["languages"]) if state["languages"] else "Unknown"
    files = state["files_changed"]

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

    Topology: classify → run_all_crews (parallel) → aggregate
    """
    g = StateGraph(ReviewState)

    g.add_node("classify",       classify)
    g.add_node("run_all_crews",  run_all_crews)
    g.add_node("aggregate",      aggregate)

    g.set_entry_point("classify")
    g.add_edge("classify",      "run_all_crews")
    g.add_edge("run_all_crews", "aggregate")
    g.add_edge("aggregate",     END)

    return g.compile()


# Build once at import — reused by cli.py
_graph = build_graph()


def run_review(diff_text: str, repo_path: str = ".", pr_number: int | None = None) -> ReviewState:
    """
    Run the full review pipeline on a diff string.

    Returns the final ReviewState with all findings and the assembled final_report.
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
    return _graph.invoke(initial)


def stream_review(diff_text: str, repo_path: str = ".", pr_number: int | None = None):
    """
    Stream node completions for live progress display in the CLI.

    Yields:
        (node_name: str, updated_state: dict) after each node completes.
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
