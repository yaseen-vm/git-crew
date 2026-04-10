"""
architecture_crew.py — CrewAI Architecture & Code Quality Review Crew

Three agents collaborate in sequence:
  1. Architecture Analyst   — evaluates structural decisions, patterns, SOLID principles
  2. Code Quality Reviewer  — examines readability, maintainability, naming, duplication
  3. Architecture Reporter  — synthesizes both agents' findings into a structured report

Why this crew exists separately from security/performance:
  Architecture issues (wrong abstraction, coupling, God objects) require a different
  mental model than security or performance. Keeping them in a separate crew with
  specialist backstories produces more focused, actionable feedback.

Public API:
  run_architecture_crew(diff_text: str) -> str
"""

from crewai import Agent, Crew, Task
from ..llm import get_langchain_llm


def run_architecture_crew(diff_text: str) -> str:
    """
    Run the Architecture Crew against a formatted diff string.

    Args:
        diff_text:  Output of format_hunks_for_review() — Markdown-formatted hunks

    Returns:
        Structured architecture findings as a Markdown string.
    """
    llm = get_langchain_llm()

    # ── Agents ────────────────────────────────────────────────────────────────

    architect = Agent(
        role="Architecture Analyst",
        goal=(
            "Evaluate the structural decisions in the changed code: "
            "are abstractions appropriate? Is coupling minimized? "
            "Are SOLID principles followed? Does the design scale?"
        ),
        backstory=(
            "You are a software architect with 15 years of experience designing "
            "large-scale systems. You spot over-engineering, premature abstraction, "
            "inappropriate design patterns, and tight coupling from a diff alone. "
            "You only comment on what changed, not pre-existing code in context lines."
        ),
        llm=llm,
        allow_delegation=False,
        verbose=True,
    )

    quality_reviewer = Agent(
        role="Code Quality Reviewer",
        goal=(
            "Examine the changed code for readability and maintainability issues: "
            "unclear naming, missing documentation for non-obvious logic, "
            "duplicated code, overly long functions, and dead code."
        ),
        backstory=(
            "You are a principal engineer who prioritizes code that teams can maintain "
            "for years. You believe naming is design, and that code is read 10x more "
            "than it is written. You give specific, actionable improvement suggestions."
        ),
        llm=llm,
        allow_delegation=False,
        verbose=True,
    )

    reporter = Agent(
        role="Architecture Reporter",
        goal=(
            "Merge the Architecture Analyst's structural findings and the Code Quality "
            "Reviewer's readability findings into a single, prioritized report."
        ),
        backstory=(
            "You translate technical architecture concerns into clear developer guidance. "
            "You rank findings by impact on long-term maintainability."
        ),
        llm=llm,
        allow_delegation=False,
        verbose=True,
    )

    # ── Tasks ─────────────────────────────────────────────────────────────────

    architecture_task = Task(
        description=(
            "Review the following git diff for architecture and design issues.\n\n"
            "Focus on:\n"
            "- Violation of SOLID principles (Single Responsibility, Open/Closed, etc.)\n"
            "- Inappropriate design patterns (or missing ones)\n"
            "- Tight coupling between modules\n"
            "- Abstractions that are too deep or too shallow\n"
            "- Missing or broken separation of concerns\n"
            "- Patterns that won't scale\n\n"
            "RULES:\n"
            "- Only comment on lines that changed (+ lines).\n"
            "- Do NOT report style preferences.\n"
            "- Include the file and approximate line for each issue.\n\n"
            f"DIFF TO REVIEW:\n{diff_text}"
        ),
        expected_output=(
            "A list of architecture concerns with file/line, issue description, "
            "and why it matters. If none, respond: 'No architecture issues found.'"
        ),
        agent=architect,
    )

    quality_task = Task(
        description=(
            "Review the same diff for code quality and readability issues.\n\n"
            "Focus on:\n"
            "- Variable/function/class names that don't reveal intent\n"
            "- Functions doing more than one thing (too long, mixed concerns)\n"
            "- Duplicated logic that should be extracted\n"
            "- Non-obvious logic with no explaining comment\n"
            "- Dead code or commented-out blocks added in this diff\n\n"
            "RULES:\n"
            "- Only comment on lines that changed (+ lines).\n"
            "- Do NOT flag existing unchanged lines.\n"
            "- Do NOT report minor style nits.\n\n"
            f"DIFF TO REVIEW:\n{diff_text}"
        ),
        expected_output=(
            "A list of code quality issues with file/line and a suggested improvement. "
            "If none, respond: 'No code quality issues found.'"
        ),
        agent=quality_reviewer,
    )

    report_task = Task(
        description=(
            "Combine the Architecture Analyst's and Code Quality Reviewer's findings "
            "into a final Markdown section, sorted by severity.\n\n"
            "Format each issue as:\n"
            "### [HIGH/MEDIUM/LOW] Issue Title\n"
            "- **File:** `path:line`\n"
            "- **Problem:** description\n"
            "- **Suggestion:** concrete improvement\n\n"
            "End with a one-line summary of total issues by severity.\n"
            "If no issues were found, write: '✅ No architecture or code quality issues found.'"
        ),
        expected_output=(
            "Complete Markdown architecture findings section, ready to embed in the final report."
        ),
        agent=reporter,
        context=[architecture_task, quality_task],
    )

    # ── Crew ──────────────────────────────────────────────────────────────────

    crew = Crew(
        agents=[architect, quality_reviewer, reporter],
        tasks=[architecture_task, quality_task, report_task],
        verbose=True,
    )

    result = crew.kickoff()
    return str(result)
