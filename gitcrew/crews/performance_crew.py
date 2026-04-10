"""
performance_crew.py — CrewAI Performance Review Crew

Three agents collaborate in sequence:
  1. Performance Profiler   — identifies computational bottlenecks in the diff
  2. Scalability Analyst    — thinks about how each issue behaves under load/scale
  3. Performance Reporter   — synthesizes findings into a structured report

Scope:
  Only flags issues with direct evidence in the changed code:
  nested loops on collections, repeated expensive calls in hot paths,
  missing database indexes on queried fields, loading full datasets when
  only a slice is needed, synchronous I/O in async contexts.

Public API:
  run_performance_crew(diff_text: str) -> str
"""

from crewai import Agent, Crew, Task

from ..llm import get_langchain_llm


def run_performance_crew(diff_text: str) -> str:
    """
    Run the Performance Crew against a formatted diff string.

    Args:
        diff_text:  Output of format_hunks_for_review() — Markdown-formatted hunks

    Returns:
        Structured performance findings as a Markdown string.
    """
    llm = get_langchain_llm()

    # ── Agents ────────────────────────────────────────────────────────────────

    profiler = Agent(
        role="Performance Profiler",
        goal=(
            "Identify concrete performance bottlenecks in the changed code: "
            "algorithmic inefficiency, unnecessary work, expensive operations "
            "called more than needed. Evidence must be visible in the diff."
        ),
        backstory=(
            "You are a performance engineer who has profiled Python, Go, and Java "
            "services at scale. You know the difference between a theoretical concern "
            "and a real bottleneck. You never flag normal code patterns as issues."
        ),
        llm=llm,
        allow_delegation=False,
        verbose=True,
    )

    scalability_analyst = Agent(
        role="Scalability Analyst",
        goal=(
            "For each bottleneck the Profiler found, describe at what scale it "
            "becomes a real problem and what the concrete impact looks like: "
            "latency spike, memory exhaustion, DB lock, CPU saturation."
        ),
        backstory=(
            "You design systems that must handle 10x traffic spikes. "
            "You translate micro-level code issues into macro-level production impact, "
            "helping teams prioritize what actually needs fixing before launch."
        ),
        llm=llm,
        allow_delegation=False,
        verbose=True,
    )

    reporter = Agent(
        role="Performance Reporter",
        goal=(
            "Combine the Profiler's findings and the Scalability Analyst's impact "
            "analysis into a clear Markdown performance report with concrete fixes."
        ),
        backstory=(
            "You write performance reports for engineering teams at pre-launch review. "
            "You rank issues by production impact, not theoretical severity."
        ),
        llm=llm,
        allow_delegation=False,
        verbose=True,
    )

    # ── Tasks ─────────────────────────────────────────────────────────────────

    profiling_task = Task(
        description=(
            "Review the following git diff for performance issues.\n\n"
            "RULES:\n"
            "- Only report issues with direct code evidence in the changed lines.\n"
            "- Do NOT report:\n"
            "  * Reading a whole file into memory for a single-use operation\n"
            "  * Using dict or TypedDict for state\n"
            "  * Sequential function calls that are not inside a loop\n"
            "  * Theoretical concerns with no visible code evidence\n"
            "- DO report:\n"
            "  * Nested loops operating on large/unbounded collections\n"
            "  * Expensive calls (DB queries, HTTP requests) inside loops\n"
            "  * Missing caching for repeated identical computations\n"
            "  * Full dataset loads when only a subset is needed\n"
            "  * Synchronous blocking calls in async/concurrent code\n"
            "  * N+1 query patterns\n\n"
            "For each issue: file, approximate line, what the issue is, "
            "quote the offending code.\n\n"
            f"DIFF TO REVIEW:\n{diff_text}"
        ),
        expected_output=(
            "List of confirmed performance bottlenecks with file/line and evidence. "
            "If none found, respond: 'No performance issues found.'"
        ),
        agent=profiler,
    )

    scalability_task = Task(
        description=(
            "For each performance issue the Profiler found:\n"
            "- At what data volume / request rate does it become a problem?\n"
            "- What is the concrete production symptom (latency, OOM, timeout, etc.)?\n"
            "- Rate severity: HIGH (fails under moderate load), "
            "MEDIUM (degrades at scale), LOW (edge case only).\n\n"
            "If the Profiler found no issues, respond: 'No scalability concerns to analyze.'"
        ),
        expected_output=(
            "For each bottleneck: scale threshold, production symptom, severity."
        ),
        agent=scalability_analyst,
        context=[profiling_task],
    )

    report_task = Task(
        description=(
            "Synthesize the Profiler's bottlenecks and the Scalability Analyst's "
            "impact analysis into a final Markdown section.\n\n"
            "Format each issue as:\n"
            "### [HIGH/MEDIUM/LOW] Issue Title\n"
            "- **File:** `path:line`\n"
            "- **Evidence:** `offending code snippet`\n"
            "- **Impact:** at what scale and what breaks\n"
            "- **Fix:** concrete improvement\n\n"
            "End with a one-line summary of counts by severity.\n"
            "If no issues were found, write: '✅ No performance issues found.'"
        ),
        expected_output=(
            "Complete Markdown performance findings section, ready to embed in the final report."
        ),
        agent=reporter,
        context=[profiling_task, scalability_task],
    )

    # ── Crew ──────────────────────────────────────────────────────────────────

    crew = Crew(
        agents=[profiler, scalability_analyst, reporter],
        tasks=[profiling_task, scalability_task, report_task],
        verbose=True,
    )

    result = crew.kickoff()
    return str(result)
