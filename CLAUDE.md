# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git Commit Rules

- Always commit as the git user `yaseen-vm` only.
- Never add `Co-Authored-By` lines or any AI attribution (no Claude, Gemini, Copilot, Codex, etc.) to commit messages.
- Keep commit messages plain — author is `yaseen-vm`, nothing else.

---

## Project: git-crew

`git-crew` is a CLI tool that reviews git diffs using three AI frameworks in combination. It analyzes only what changed (diff-aware), routes findings through specialist crews, and provides an interactive Q&A session after the report.

**Quick start:**
```bash
pip install -r requirements.txt
export GROQ_API_KEY=your_key_here
git-crew review                   # review staged changes
git-crew review HEAD~3..HEAD      # review last 3 commits
git-crew pr 42                    # review a GitHub PR
```

---

## Why Three Frameworks (The Core Architectural Argument)

Each framework is used for what it is uniquely good at. They do not overlap.

| Framework | Role in git-crew | Why it fits |
|---|---|---|
| **LangGraph** | Top-level pipeline orchestration | Stateful, conditional, multi-step workflow. classify → 3 crews → aggregate. Skips crews when not needed. Streams progress events. |
| **CrewAI** | Deep specialist reviews (3 separate crews) | Role-based agent teams with delegation. A 3-agent crew (Analyst → Domain Expert → Reporter) produces richer, better-structured findings than a single-agent chain. |
| **AutoGen** | Post-review interactive Q&A | `UserProxy + AssistantAgent` with `human_input_mode="ALWAYS"` is the exact pattern for developer-asks, AI-answers back-and-forth. CrewAI and LangGraph cannot do live user input. |

If you are asked why all three frameworks are used: the answer is that they are used in genuinely different contexts, not as redundant alternatives.

---

## Architecture: Data Flow

```
git diff / gh pr diff
        ↓
   [git.py]  parse_diff() → list[DiffHunk]
             format_hunks_for_review() → Markdown string
             diff_summary() → metadata dict
        ↓
   [orchestrator.py]  LangGraph StateGraph
        ↓
   ┌────────────────────────────────────────┐
   │  classify node                          │
   │  • Parses hunks, gets metadata          │
   │  • Sets: run_security, run_architecture │
   │           run_performance, is_docs_only │
   └────────────────────────────────────────┘
        ↓
   ┌────────────────────────────────────────┐
   │  security_review node                   │
   │  • Calls CrewAI Security Crew (3 agents)│
   │  • Skips if run_security = False        │
   └────────────────────────────────────────┘
        ↓
   ┌────────────────────────────────────────┐
   │  architecture_review node               │
   │  • Calls CrewAI Architecture Crew       │
   │  • Skips if run_architecture = False    │
   └────────────────────────────────────────┘
        ↓
   ┌────────────────────────────────────────┐
   │  performance_review node                │
   │  • Calls CrewAI Performance Crew        │
   │  • Skips if run_performance = False     │
   └────────────────────────────────────────┘
        ↓
   ┌────────────────────────────────────────┐
   │  aggregate node                         │
   │  • Merges all three findings            │
   │  • Builds final_report Markdown string  │
   └────────────────────────────────────────┘
        ↓
   [report.py]  Rich terminal render
   [report.py]  Optional: save to .md file
   [report.py]  Optional: post as GitHub PR comment
        ↓
   [interactive.py]  AutoGen Q&A session
   Developer types questions → ReviewerAgent answers
```

---

## File-by-File Reference

### `gitcrew/git.py`

**Purpose:** All git interaction and diff parsing. No LLM calls.

Key exports:
- `DiffHunk` — dataclass for one changed code block. Fields: `file_path`, `language`, `start_line`, `hunk_header`, `raw`, `added_lines`, `removed_lines`, `is_security_file`
- `parse_diff(diff_text: str) → list[DiffHunk]` — parses raw `git diff` output. Handles multiple files, multiple hunks per file, new/deleted files. Skips binary files.
- `get_staged_diff(repo_path)` — wraps `git diff --cached -U3`
- `get_working_tree_diff(repo_path)` — wraps `git diff -U3`
- `get_commit_range_diff(range, repo_path)` — wraps `git diff -U3 <range>`
- `get_pr_diff(pr_number)` — wraps `gh pr diff <n>` (requires `gh auth login`)
- `format_hunks_for_review(hunks, max_chars=14000)` — renders hunks as a Markdown string for LLM prompts. Truncates at max_chars with a notice.
- `diff_summary(hunks) → dict` — returns `files`, `languages`, `has_security_files`, `is_docs_only`, `total_added`, `total_removed`, `hunk_count`

Security detection: `_is_security_file()` checks if any segment of the file path contains keywords like `auth`, `password`, `token`, `jwt`, `crypto`, etc. This flag forces the Security Crew to always run even on small diffs.

Language detection: `_EXT_LANG` dict maps file extensions to display names. Used to annotate code fences in `format_hunks_for_review`.

---

### `gitcrew/orchestrator.py`

**Purpose:** LangGraph `StateGraph` that sequences all review steps.

**ReviewState fields:**

| Field | Type | Set by |
|---|---|---|
| `diff_text` | str | caller (input) |
| `repo_path` | str | caller (input) |
| `pr_number` | int \| None | caller (input) |
| `formatted_diff` | str | classify node |
| `files_changed` | list[str] | classify node |
| `languages` | list[str] | classify node |
| `has_security_files` | bool | classify node |
| `is_docs_only` | bool | classify node |
| `run_security` | bool | classify node |
| `run_architecture` | bool | classify node |
| `run_performance` | bool | classify node |
| `security_findings` | str | security_review node |
| `architecture_findings` | str | architecture_review node |
| `performance_findings` | str | performance_review node |
| `final_report` | str | aggregate node |

**Node responsibilities:**

- `classify` — calls `parse_diff` + `diff_summary`, sets routing flags. docs-only diffs skip all crews. Security-sensitive files force `run_security = True`.
- `security_review` — checks `run_security` flag; if True, calls `run_security_crew(formatted_diff)`
- `architecture_review` — checks `run_architecture` flag; calls `run_architecture_crew()`
- `performance_review` — checks `run_performance` flag; calls `run_performance_crew()`
- `aggregate` — assembles `final_report` Markdown from all findings + metadata

**Public API:**
- `run_review(diff_text, repo_path, pr_number) → ReviewState` — blocking, returns final state
- `stream_review(diff_text, repo_path, pr_number)` — generator, yields `(node_name, state_delta)` after each node. Used by cli.py for live progress display.
- `build_graph()` — builds and compiles the graph. Called once at import time (`_graph = build_graph()`).

---

### `gitcrew/crews/security_crew.py`

**Purpose:** CrewAI Security Review Crew — 3 agents, 3 tasks.

**Agents:**
1. `Security Auditor` — scans for vulnerability patterns with direct code evidence. RULES: only + lines, no env-var flagging, no CLI path flagging. Flags: SQL injection, hardcoded secrets, eval on user input, shell=True with user data, missing auth checks.
2. `Exploit Analyst` — receives Auditor's findings via `context=[audit_task]`. For each vulnerability: attacker profile, impact, difficulty, CVSS-style severity.
3. `Security Reporter` — receives both via `context=[audit_task, exploit_task]`. Outputs structured Markdown section.

**Task context chain:** `audit_task → exploit_task (context=[audit]) → report_task (context=[audit, exploit])`

This is the key CrewAI pattern: each agent builds on the previous without repeating the full prompt. The Reporter gets a richer input than any single agent could produce alone.

**Public API:** `run_security_crew(diff_text: str) → str`

---

### `gitcrew/crews/architecture_crew.py`

**Purpose:** CrewAI Architecture & Code Quality Crew — 3 agents, 3 tasks.

**Agents:**
1. `Architecture Analyst` — SOLID violations, coupling, inappropriate patterns, abstractions that are too deep/shallow, non-scalable designs
2. `Code Quality Reviewer` — unclear naming, functions doing too many things, duplicated logic, non-obvious logic without comments, dead code in the diff
3. `Architecture Reporter` — synthesizes both into severity-ranked Markdown

**Why separate from Security Crew:** Architecture requires a different mental model. A security-focused agent would miss design-level issues; an architecture-focused agent would miss security patterns. Separate crews with specialist backstories produce focused, non-confused output.

**Public API:** `run_architecture_crew(diff_text: str) → str`

---

### `gitcrew/crews/performance_crew.py`

**Purpose:** CrewAI Performance Review Crew — 3 agents, 3 tasks.

**Agents:**
1. `Performance Profiler` — only flags issues with direct code evidence: nested loops on collections, expensive calls in loops, N+1 queries, full-dataset loads, sync I/O in async contexts. Explicitly does NOT flag: reading a whole file once, sequential function calls outside loops, TypedDicts.
2. `Scalability Analyst` — takes Profiler findings, describes production impact: at what data volume/request rate does this break, what fails (latency, OOM, timeout, lock)
3. `Performance Reporter` — severity-ranked Markdown with concrete fixes

**Public API:** `run_performance_crew(diff_text: str) → str`

---

### `gitcrew/interactive.py`

**Purpose:** AutoGen post-review interactive Q&A session.

**Two-agent setup:**
- `ReviewerAgent` (AssistantAgent) — loaded with `final_report` + first 8000 chars of `diff_text` in its system message. Answers questions, explains findings, writes corrected code.
- `DeveloperProxy` (UserProxyAgent) — `human_input_mode="ALWAYS"`, `max_consecutive_auto_reply=0`. The developer types every message. No code execution.

**LLM config:** AutoGen uses Groq via OpenAI-compatible endpoint:
```python
{
    "model": "llama-3.3-70b-versatile",
    "api_key": GROQ_API_KEY,
    "base_url": "https://api.groq.com/openai/v1",
    "api_type": "openai",
}
```

**Termination:** Either agent returns True from `is_termination_msg` when the message content (lowercased, stripped) is in `{"exit", "done", "quit", "bye", "q", "no", "thanks"}`. Also terminates after `max_turns=20`.

**Why AutoGen (not CrewAI or LangGraph):**
- CrewAI: scripted task sequences, no real-time input
- LangGraph: automated pipelines, not REPL sessions
- AutoGen's `UserProxy + AssistantAgent` with `human_input_mode="ALWAYS"` is designed exactly for this pattern

**Public API:** `start_interactive_session(final_report, diff_text, max_turns=20)`

---

### `gitcrew/report.py`

**Purpose:** All output rendering and delivery. No LLM calls.

Key functions:
- `print_header(source_label, file_count)` — Rich panel with source info
- `print_step(node_name)` — progress tick per LangGraph node
- `print_skip_notice(is_docs_only)` — explains why crews were skipped
- `print_report(final_report)` — renders Markdown via `rich.markdown.Markdown`
- `save_report(final_report, output_path)` — writes UTF-8 `.md` file
- `post_pr_comment(pr_number, final_report)` — calls `gh pr comment <n> --body <report>`
- `print_error(message)` — formats error for terminal

---

### `gitcrew/cli.py`

**Purpose:** Typer CLI. Two main commands, two hook management commands.

**Commands:**

`review [COMMIT_RANGE]`
- No argument → staged diff (`git diff --cached`)
- `HEAD~3..HEAD` → commit range diff
- `--unstaged` → working-tree diff
- `--output FILE` → save report
- `--no-interactive` → skip AutoGen Q&A (required for CI/hooks)
- `--repo PATH` → repo root (default: `.`)

`pr PR_NUMBER`
- Fetches diff via `gh pr diff`
- `--post-comment` → posts report as GitHub PR comment
- `--output FILE`, `--no-interactive` same as review

`install-hook`
- Writes `_HOOK_SCRIPT` to `.git/hooks/pre-push` with chmod 755
- Hook runs `git-crew review --no-interactive`; exit 1 blocks the push

`uninstall-hook`
- Removes `.git/hooks/pre-push`

**Pipeline flow in `_run_review_pipeline` (shared by review + pr):**
1. Validate diff is non-empty
2. `print_header`
3. `stream_review` → tick each node with `print_step`
4. `print_report`
5. `save_report` if `--output`
6. Prompt for interactive session if `sys.stdin.isatty()` and not `--no-interactive`

---

## Setup

Requires **Python 3.11+**.

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
.venv\Scripts\activate           # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set environment variables
cp .env.example .env
# Edit .env: add GROQ_API_KEY

# 4. Install as an editable package (enables `git-crew` command in the venv)
pip install -e .
# Or run directly without installing:
python -m gitcrew.cli review
```

---

## Running

After `pip install -e .`, the `git-crew` command is available. You can also run `python -m gitcrew.cli <command>` without installing.

```bash
# Review staged changes
git-crew review

# Review last 3 commits
git-crew review HEAD~3..HEAD

# Review a branch diff against main
git-crew review main..my-feature

# Review unstaged working-tree changes
git-crew review --unstaged

# Review a GitHub PR
git-crew pr 42

# Review PR and post findings as PR comment
git-crew pr 42 --post-comment

# Save report to file
git-crew review -o report.md

# Non-interactive (for CI, hooks)
git-crew review --no-interactive

# Install as pre-push git hook
git-crew install-hook
```

---

## Framework Responsibility Matrix (Quick Reference)

```
WHAT TASK                          → WHICH FRAMEWORK
─────────────────────────────────────────────────────
Parse git diff into structured data → git.py (no AI)
Route between review types          → LangGraph (orchestrator.py)
Maintain state across pipeline      → LangGraph (ReviewState TypedDict)
Stream live progress to terminal    → LangGraph (.stream())
Security vulnerability analysis     → CrewAI Security Crew
Exploit impact analysis             → CrewAI Security Crew (agent 2)
Architecture pattern review         → CrewAI Architecture Crew
Code quality / readability review   → CrewAI Architecture Crew (agent 2)
Performance bottleneck analysis     → CrewAI Performance Crew
Scalability impact assessment       → CrewAI Performance Crew (agent 2)
Assemble final report               → LangGraph aggregate node
Interactive developer Q&A           → AutoGen (interactive.py)
Render terminal output              → report.py + Rich (no AI)
Post GitHub PR comment              → report.py + gh CLI (no AI)
```

---

## Key Design Decisions

**1. Diff-aware, not file-aware**
Only changed hunks (lines starting with `+`) are sent to crews. Context lines (space-prefixed) are included for readability but crew agents are instructed to ignore them. This matches how real PR review tools work and keeps LLM context small.

**2. Sequential crews (not parallel)**
The three CrewAI crews run sequentially in the LangGraph graph. Parallel execution is possible with LangGraph's `Send` API but adds complexity without significant benefit for a review tool — crews don't share intermediate state.

**3. One LLM for all frameworks (Groq / llama-3.3-70b-versatile)**
LangGraph uses `ChatGroq` via LangChain. CrewAI agents accept a `langchain_groq.ChatGroq` instance as their `llm` parameter. AutoGen is pointed at Groq's OpenAI-compatible endpoint. One API key, one model, consistent behavior.

**4. Crew agents have hard rules in their prompts**
Every crew agent has explicit RULES sections: what to flag, what to ignore. This prevents hallucinated issues (e.g., flagging `os.environ.get("KEY")` as a security risk — which is actually the correct pattern).

**5. `is_security_file` flag in DiffHunk**
Computed at parse time from path keywords. Used by the `classify` node to force the Security Crew even on small diffs. Stored on the DiffHunk so it's visible in `format_hunks_for_review` output (annotated with ⚠️).

**6. `is_docs_only` short-circuit**
If every changed file has a `.md`, `.txt`, or `.rst` extension, all three crews are skipped. The aggregate node still runs and produces a report explaining the skip. This prevents LLM calls on pure documentation PRs.

---

## Extending the Project

**Add a new crew (e.g., Test Coverage Crew):**
1. Create `gitcrew/crews/test_crew.py` — follow the same 3-agent pattern
2. Add `run_test_crew` to its public API
3. Add `test_findings: str` to `ReviewState` in `orchestrator.py`
4. Add a `test_review` node that calls `run_test_crew(state["formatted_diff"])`
5. Insert the node between `performance_review` and `aggregate` with `g.add_edge`
6. Add `run_tests: bool` flag logic in the `classify` node
7. Include `test_findings` in the `aggregate` node's report

**Change the LLM:**
All three frameworks are configured in one place each:
- LangGraph/CrewAI: `_get_llm()` in each crew file returns a `ChatGroq` instance
- AutoGen: `_get_llm_config()` in `interactive.py` returns the config dict
Change the `model` string to switch models. To use OpenAI: swap `ChatGroq` for `ChatOpenAI` and update the AutoGen config.

**Add a new output channel (e.g., Slack):**
Add a function to `report.py` and call it from `_run_review_pipeline` in `cli.py` based on a new `--slack-webhook` option.

---

## Environment Variables

| Variable | Required | Used by |
|---|---|---|
| `GROQ_API_KEY` | Yes | All LLM calls — CrewAI crews, AutoGen interactive session |

Optional (for PR features):
- `gh auth login` must be completed for `git-crew pr` and `--post-comment` to work. This is not an env var — it's stored in `gh`'s credential store.
