"""
security_crew.py — CrewAI Security Review Crew

Three agents collaborate in sequence:
  1. Security Auditor    — scans changed code for known vulnerability patterns
  2. Exploit Analyst     — thinks offensively: how could each finding be abused?
  3. Security Reporter   — synthesizes both agents' work into a structured report

Why CrewAI here:
  The delegation model lets the Exploit Analyst build on the Auditor's raw findings
  without the Auditor needing to repeat itself. The Reporter gets both outputs via
  CrewAI's task context mechanism, producing richer output than a single-agent chain.

Public API:
  run_security_crew(diff_text: str) -> str
"""

import os
from crewai import Agent, Crew, Task
from langchain_groq import ChatGroq


def _get_llm() -> ChatGroq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise EnvironmentError("GROQ_API_KEY is not set. Get a free key at https://console.groq.com")
    return ChatGroq(model="llama-3.3-70b-versatile", temperature=0.1, api_key=api_key)


def run_security_crew(diff_text: str) -> str:
    """
    Run the Security Crew against a formatted diff string.

    Args:
        diff_text:  Output of format_hunks_for_review() — Markdown-formatted hunks

    Returns:
        Structured security findings as a Markdown string.
        Returns a "no issues" message if the crew finds nothing.
    """
    llm = _get_llm()

    # ── Agents ────────────────────────────────────────────────────────────────

    auditor = Agent(
        role="Security Auditor",
        goal=(
            "Identify real security vulnerabilities in changed code — "
            "injection flaws, hardcoded secrets, missing validation, insecure defaults. "
            "Only report issues with direct evidence in the diff. Never speculate."
        ),
        backstory=(
            "You are a senior application security engineer with 10 years of experience "
            "doing code security reviews for fintech and healthcare companies. "
            "You focus exclusively on code that changed — you never flag pre-existing issues "
            "in unchanged context lines."
        ),
        llm=llm,
        allow_delegation=False,
        verbose=True,
    )

    exploit_analyst = Agent(
        role="Exploit Analyst",
        goal=(
            "For each vulnerability the Security Auditor found, describe the concrete "
            "attack scenario: who can exploit it, what do they gain, how hard is it. "
            "Assign a CVSS-style severity (CRITICAL / HIGH / MEDIUM / LOW)."
        ),
        backstory=(
            "You are a penetration tester who thinks like an attacker. "
            "You take vulnerability reports and make their real-world impact clear, "
            "so developers understand why each issue must be fixed."
        ),
        llm=llm,
        allow_delegation=False,
        verbose=True,
    )

    reporter = Agent(
        role="Security Reporter",
        goal=(
            "Combine the audit findings and exploit analysis into a clear, "
            "developer-readable Markdown security report with actionable fixes."
        ),
        backstory=(
            "You write security reports for engineering teams. "
            "You present findings concisely: severity badge, affected line, "
            "attack scenario, and a concrete code-level fix."
        ),
        llm=llm,
        allow_delegation=False,
        verbose=True,
    )

    # ── Tasks ─────────────────────────────────────────────────────────────────

    audit_task = Task(
        description=(
            "Review the following git diff for security vulnerabilities.\n\n"
            "RULES:\n"
            "- Only report issues visible in the changed lines (+ lines).\n"
            "- Do NOT flag: reading secrets from env vars (correct practice), "
            "CLI tools reading user-specified paths, normal stdout output.\n"
            "- DO flag: SQL/command injection, hardcoded secrets as string literals, "
            "eval() on user input, unsafe deserialization, shell=True with user data, "
            "missing authentication/authorization checks.\n\n"
            "For each issue:\n"
            "  File: <path>\n"
            "  Line: <approximate line number>\n"
            "  Issue: <name>\n"
            "  Evidence: <quote the offending line>\n\n"
            f"DIFF TO REVIEW:\n{diff_text}"
        ),
        expected_output=(
            "A numbered list of confirmed security issues with File, Line, Issue, "
            "and Evidence fields. If none found, respond: 'No security issues found.'"
        ),
        agent=auditor,
    )

    exploit_task = Task(
        description=(
            "Using the Security Auditor's findings, describe the exploit scenario "
            "for each vulnerability:\n"
            "- Who is the attacker (unauthenticated user / authenticated user / insider)?\n"
            "- What do they gain (data exfiltration / RCE / privilege escalation / DoS)?\n"
            "- How difficult is exploitation (trivial / moderate / complex)?\n"
            "- Assign severity: CRITICAL, HIGH, MEDIUM, or LOW.\n\n"
            "If the audit found no issues, respond: 'No exploitable issues to analyze.'"
        ),
        expected_output=(
            "For each vulnerability: attacker profile, impact, difficulty, severity label."
        ),
        agent=exploit_analyst,
        context=[audit_task],
    )

    report_task = Task(
        description=(
            "Synthesize the Security Auditor's findings and the Exploit Analyst's impact "
            "assessments into a final Markdown section.\n\n"
            "Format each issue as:\n"
            "### [SEVERITY] Issue Title\n"
            "- **File:** `path:line`\n"
            "- **Evidence:** `offending code`\n"
            "- **Attack:** scenario description\n"
            "- **Fix:** concrete remediation\n\n"
            "End with a one-line summary: total count by severity.\n"
            "If nothing was found by either agent, write: '✅ No security issues found.'"
        ),
        expected_output=(
            "Complete Markdown security findings section, ready to embed in the final report."
        ),
        agent=reporter,
        context=[audit_task, exploit_task],
    )

    # ── Crew ──────────────────────────────────────────────────────────────────

    crew = Crew(
        agents=[auditor, exploit_analyst, reporter],
        tasks=[audit_task, exploit_task, report_task],
        verbose=True,
    )

    result = crew.kickoff()
    return str(result)
