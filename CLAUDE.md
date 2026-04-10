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
pip install -e .
cp .env.example .env   # set LLM_PROVIDER + matching API key
git-crew review        # review staged changes
git-crew review HEAD~3..HEAD
git-crew pr 42
```

---

## Why Three Frameworks (The Core Architectural Argument)

Each framework is used for what it is uniquely good at. They do not overlap.

| Framework | Role in git-crew | Why it fits |
|---|---|---|
| **LangGraph** | Top-level pipeline orchestration | Stateful, conditional, multi-step workflow. classify → run_all_crews → aggregate. Skips crews when not needed. Streams progress events. |
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
   ┌────────────────────────────────────────────────────────┐
   │  run_all_crews node  (ThreadPoolExecutor, max_workers=3)│
   │  • Security Crew   ┐                                   │
   │  • Architecture    ├── run in parallel                 │
   │  • Performance     ┘                                   │
   │  • Skipped crews return "" immediately                 │
   │  • Each crew failure → warning string, not abort       │
   └────────────────────────────────────────────────────────┘
        ↓
   ┌────────────────────────────────────────┐
   │  aggregate node                         │
   │  • Merges all three findings            │
   │  • Counts severity labels (CRITICAL…)  │
   │  • Builds final_report Markdown string  │
   └────────────────────────────────────────┘
        ↓
   [report.py]  Rich terminal render
   [report.py]  Optional: save to .md file (--output)
   [report.py]  Optional: save SARIF 2.1.0 (--sarif)
   [report.py]  Optional: post as GitHub PR comment
        ↓
   [interactive.py]  AutoGen Q&A session
   Developer types questions → ReviewerAgent answers
```

---

## File-by-File Reference

### `gitcrew/llm.py`

**Purpose:** Single provider factory used by all three frameworks. The only place where LLM provider configuration lives.

**How to switch providers:** set two env vars in `.env`, no code changes needed.

```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

**Supported providers:**

| `LLM_PROVIDER` | Key env var | Default model |
|---|---|---|
| `groq` (default) | `GROQ_API_KEY` | `llama-3.3-70b-versatile` |
| `openai` | `OPENAI_API_KEY` | `gpt-4o` |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-3-5-sonnet-20241022` |
| `ollama` | _(none)_ | `llama3.3` |
| `azure` | `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT` | `gpt-4o` |
| `mistral` | `MISTRAL_API_KEY` | `mistral-large-latest` |
| `google` | `GOOGLE_API_KEY` | `gemini-2.0-flash` |
| `openrouter` | `OPENROUTER_API_KEY` | `openai/gpt-4o` |
| `together` | `TOGETHER_API_KEY` | `meta-llama/Llama-3-70b-chat-hf` |

Override model: `LLM_MODEL=gpt-4-turbo` in `.env`.

**Public API:**
- `get_langchain_llm(temperature=0.1)` — returns a LangChain `BaseChatModel`. Used by CrewAI crew agents via `agent.llm`.
- `get_autogen_config() → dict` — returns AutoGen `llm_config` dict. Used by `interactive.py`.
- `describe_active() → str` — returns `"provider / model"` for display in the CLI header.

**Install the matching LangChain package:**
```bash
pip install "git-crew[openai]"        # openai, azure, openrouter, together
pip install "git-crew[anthropic]"
pip install "git-crew[ollama]"
pip install "git-crew[mistral]"
pip install "git-crew[google]"
pip install "git-crew[all-providers]" # everything
```

---

### `gitcrew/git.py`

**Purpose:** All git interaction and diff parsing. No LLM calls.

Key exports:
- `DiffHunk` — dataclass for one changed code block. Fields: `file_path`, `language`, `start_line`, `hunk_header`, `raw`, `added_lines`, `removed_lines`, `is_security_file`
- `parse_diff(diff_text: str) → list[DiffHunk]` — parses raw `git diff` output. Handles multiple files, multiple hunks per file, new/deleted files. Skips binary files.
- `get_staged_diff(repo_path)` — wraps `git diff --cached -U3`
- `get_working_tree_diff(repo_path)` — wraps `git diff -U3`
- `get_commit_range_diff(range, repo_path)` — wraps `git diff -U3 <range>`
- `get_pr_diff(pr_number)` — wraps `gh pr diff <n>`. In GitHub Actions, set `GH_TOKEN`; locally run `gh auth login`.
- `format_hunks_for_review(hunks, max_chars=14000)` — renders hunks as a Markdown string for LLM prompts. Truncates at max_chars with a notice.
- `diff_summary(hunks) → dict` — returns `files`, `languages`, `has_security_files`, `is_docs_only`, `total_added`, `total_removed`, `hunk_count`

Security detection: `_is_security_file()` checks path segments against keywords like `auth`, `password`, `token`, `jwt`, `crypto`, etc. Forces the Security Crew to always run.

Language detection: `_EXT_LANG` dict maps file extensions to display names.

---

### `gitcrew/orchestrator.py`

**Purpose:** LangGraph `StateGraph` that sequences all review steps.

**Graph topology:** `START → classify → run_all_crews → aggregate → END`

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
| `security_findings` | str | run_all_crews node |
| `architecture_findings` | str | run_all_crews node |
| `performance_findings` | str | run_all_crews node |
| `final_report` | str | aggregate node |

**Node responsibilities:**

- `classify` — calls `parse_diff` + `diff_summary`, sets routing flags. docs-only diffs skip all crews. Security-sensitive files force `run_security = True`.
- `run_all_crews` — uses `ThreadPoolExecutor(max_workers=3)` to run all enabled CrewAI crews in parallel. Each crew's future is caught individually — one failure returns a warning string and doesn't abort the others.
- `aggregate` — scans findings text for `[CRITICAL]`/`**CRITICAL**` etc. to count severities, assembles `final_report` Markdown.

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

LLM obtained via `get_langchain_llm()` from `llm.py` — provider determined by `LLM_PROVIDER` env var.

**Public API:** `run_security_crew(diff_text: str) → str`

---

### `gitcrew/crews/architecture_crew.py`

**Purpose:** CrewAI Architecture & Code Quality Crew — 3 agents, 3 tasks.

**Agents:**
1. `Architecture Analyst` — SOLID violations, coupling, inappropriate patterns, abstractions that are too deep/shallow, non-scalable designs
2. `Code Quality Reviewer` — unclear naming, functions doing too many things, duplicated logic, non-obvious logic without comments, dead code in the diff
3. `Architecture Reporter` — synthesizes both into severity-ranked Markdown

LLM obtained via `get_langchain_llm()` from `llm.py`.

**Public API:** `run_architecture_crew(diff_text: str) → str`

---

### `gitcrew/crews/performance_crew.py`

**Purpose:** CrewAI Performance Review Crew — 3 agents, 3 tasks.

**Agents:**
1. `Performance Profiler` — only flags issues with direct code evidence: nested loops on collections, expensive calls in loops, N+1 queries, full-dataset loads, sync I/O in async contexts. Explicitly does NOT flag: reading a whole file once, sequential function calls outside loops, TypedDicts.
2. `Scalability Analyst` — takes Profiler findings, describes production impact: at what data volume/request rate does this break, what fails (latency, OOM, timeout, lock)
3. `Performance Reporter` — severity-ranked Markdown with concrete fixes

LLM obtained via `get_langchain_llm()` from `llm.py`.

**Public API:** `run_performance_crew(diff_text: str) → str`

---

### `gitcrew/interactive.py`

**Purpose:** AutoGen post-review interactive Q&A session.

**Two-agent setup:**
- `ReviewerAgent` (AssistantAgent) — loaded with `final_report` + first 8000 chars of `diff_text` in its system message. Answers questions, explains findings, writes corrected code.
- `DeveloperProxy` (UserProxyAgent) — `human_input_mode="ALWAYS"`, `max_consecutive_auto_reply=0`. The developer types every message. No code execution.

**LLM config:** obtained via `get_autogen_config()` from `llm.py`. Respects `LLM_PROVIDER` / `LLM_MODEL` like all other modules. For non-OpenAI providers, uses their OpenAI-compatible endpoints where available, or AutoGen's native `api_type` (e.g. `anthropic`, `azure`).

**Termination:** Either agent returns True from `is_termination_msg` when message content (lowercased, stripped) is in `{"exit", "done", "quit", "bye", "q", "no", "thanks"}`. Also terminates after `max_turns=20`.

**Why AutoGen (not CrewAI or LangGraph):**
- CrewAI: scripted task sequences, no real-time input
- LangGraph: automated pipelines, not REPL sessions
- AutoGen's `UserProxy + AssistantAgent` with `human_input_mode="ALWAYS"` is designed exactly for this pattern

**Public API:** `start_interactive_session(final_report, diff_text, max_turns=20)`

---

### `gitcrew/report.py`

**Purpose:** All output rendering and delivery. No LLM calls.

Key functions:
- `print_header(source_label, file_count)` — Rich panel showing source, file count, and active `LLM_PROVIDER / model` from `llm.describe_active()`
- `print_step(node_name)` — progress tick per LangGraph node. Maps `run_all_crews` → `"Running security · architecture · performance crews (parallel)"`
- `print_skip_notice(is_docs_only)` — explains why crews were skipped
- `print_report(final_report)` — renders Markdown via `rich.markdown.Markdown`
- `save_report(final_report, output_path)` — writes UTF-8 `.md` file
- `save_sarif(state, output_path)` — writes SARIF 2.1.0 JSON. One result per crew section. Severity mapped from highest label found (`CRITICAL/HIGH → error`, `MEDIUM → warning`, else `note`). Compatible with `github/codeql-action/upload-sarif`.
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
- `--output FILE` → save Markdown report
- `--sarif FILE` → save SARIF 2.1.0 file for GitHub Code Scanning
- `--no-interactive` → skip AutoGen Q&A (required for CI/hooks)
- `--repo PATH` → repo root (default: `.`)

`pr PR_NUMBER`
- Fetches diff via `gh pr diff`
- `--post-comment` → posts report as GitHub PR comment
- `--output FILE`, `--sarif FILE`, `--no-interactive` same as review

`install-hook`
- Writes `_HOOK_SCRIPT` to `.git/hooks/pre-push` with chmod 755
- Hook runs `git-crew review --no-interactive`; exit 1 blocks the push

`uninstall-hook`
- Removes `.git/hooks/pre-push`

**Pipeline flow in `_run_review_pipeline` (shared by review + pr):**
1. Validate diff is non-empty
2. `print_header` (shows active provider/model)
3. `stream_review` → tick each node with `print_step`
4. `print_report`
5. `save_report` if `--output`
6. `save_sarif` if `--sarif`
7. Prompt for interactive session if `sys.stdin.isatty()` and not `--no-interactive`

---

## Setup

Requires **Python 3.11+**.

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
.venv\Scripts\activate           # Windows

# 2. Install core dependencies (includes Groq by default)
pip install -e .

# 3. Install provider package if not using Groq
pip install "git-crew[openai]"        # for OpenAI / Azure / OpenRouter / Together
pip install "git-crew[anthropic]"     # for Anthropic Claude
pip install "git-crew[ollama]"        # for local Ollama (no key needed)
pip install "git-crew[all-providers]" # install everything

# 4. Configure your provider
cp .env.example .env
# Edit .env: set LLM_PROVIDER and the matching API key
```

---

## Running

```bash
git-crew review                        # staged changes
git-crew review HEAD~3..HEAD           # commit range
git-crew review main..feature          # branch diff
git-crew review --unstaged             # working-tree changes
git-crew pr 42                         # review a GitHub PR
git-crew pr 42 --post-comment          # post findings as PR comment
git-crew review -o report.md           # save Markdown report
git-crew review --sarif results.sarif  # save SARIF for GitHub Code Scanning
git-crew review --no-interactive       # non-interactive (CI/hooks)
git-crew install-hook                  # install as pre-push hook
```

---

## Framework Responsibility Matrix (Quick Reference)

```
WHAT TASK                          → WHICH MODULE / FRAMEWORK
──────────────────────────────────────────────────────────────────
Parse git diff into structured data → git.py (no AI)
Provide LLM to all frameworks       → llm.py (provider factory)
Route between review types          → LangGraph (orchestrator.py)
Maintain state across pipeline      → LangGraph (ReviewState TypedDict)
Stream live progress to terminal    → LangGraph (.stream())
Run crews in parallel               → run_all_crews node (ThreadPoolExecutor)
Security vulnerability analysis     → CrewAI Security Crew
Exploit impact analysis             → CrewAI Security Crew (agent 2)
Architecture pattern review         → CrewAI Architecture Crew
Code quality / readability review   → CrewAI Architecture Crew (agent 2)
Performance bottleneck analysis     → CrewAI Performance Crew
Scalability impact assessment       → CrewAI Performance Crew (agent 2)
Assemble final report               → LangGraph aggregate node
Interactive developer Q&A           → AutoGen (interactive.py)
Render terminal output              → report.py + Rich (no AI)
Export SARIF for Code Scanning      → report.py save_sarif() (no AI)
Post GitHub PR comment              → report.py + gh CLI (no AI)
```

---

## Key Design Decisions

**1. Diff-aware, not file-aware**
Only changed hunks (lines starting with `+`) are sent to crews. Context lines (space-prefixed) are included for readability but crew agents are instructed to ignore them. This matches how real PR review tools work and keeps LLM context small.

**2. Parallel crews (ThreadPoolExecutor)**
All three CrewAI crews run concurrently inside the single `run_all_crews` LangGraph node. Total review time ≈ `max(t_security, t_architecture, t_performance)` rather than their sum. Each crew future is wrapped in try/except — one failure does not abort the others.

**3. Single LLM factory for all frameworks (`llm.py`)**
`get_langchain_llm()` is called by all three crew files. `get_autogen_config()` is called by `interactive.py`. Switching providers requires only changing `LLM_PROVIDER` in `.env` — no code changes in any crew or framework file. 9 providers supported: groq, openai, anthropic, ollama, azure, mistral, google, openrouter, together.

**4. Crew agents have hard rules in their prompts**
Every crew agent has explicit RULES sections: what to flag, what to ignore. This prevents hallucinated issues (e.g., flagging `os.environ.get("KEY")` as a security risk — which is actually the correct pattern).

**5. `is_security_file` flag in DiffHunk**
Computed at parse time from path keywords. Used by the `classify` node to force the Security Crew even on small diffs. Stored on the DiffHunk so it's visible in `format_hunks_for_review` output (annotated with ⚠️).

**6. `is_docs_only` short-circuit**
If every changed file has a `.md`, `.txt`, or `.rst` extension, all three crews are skipped. The aggregate node still runs and produces a report explaining the skip. This prevents LLM calls on pure documentation PRs.

**7. SARIF output**
`report.save_sarif()` emits SARIF 2.1.0, the same format used by CodeQL, Snyk, and Semgrep. Severity is determined by scanning findings text for the highest label (`CRITICAL/HIGH → error`, `MEDIUM → warning`, else `note`). Compatible with `github/codeql-action/upload-sarif` for GitHub Code Scanning integration.

---

## Extending the Project

**Add a new crew (e.g., Test Coverage Crew):**
1. Create `gitcrew/crews/test_crew.py` — follow the same 3-agent pattern. Call `get_langchain_llm()` from `..llm`.
2. Add `run_test_crew` to its public API
3. Add `test_findings: str` and `run_tests: bool` to `ReviewState` in `orchestrator.py`
4. In `run_all_crews`, submit the new crew to the executor: `if state["run_tests"]: tasks["test"] = executor.submit(run_test_crew, formatted)`
5. Add routing logic in `classify`: `run_tests = not is_docs_only`
6. Include `test_findings` in the `aggregate` node's report

**Add a new LLM provider:**
All provider logic is in `gitcrew/llm.py`. Add an entry to `_PROVIDERS`, add a branch in `get_langchain_llm()`, and add a branch in `get_autogen_config()`. If the provider uses an OpenAI-compatible endpoint, both functions follow an identical pattern already shown for openrouter/together.

**Add a new output channel (e.g., Slack):**
Add a function to `report.py` and call it from `_run_review_pipeline` in `cli.py` based on a new `--slack-webhook` option.

---

## Environment Variables

| Variable | Required | Notes |
|---|---|---|
| `LLM_PROVIDER` | No | Default: `groq`. See `llm.py` for all options. |
| `LLM_MODEL` | No | Overrides the provider's default model. |
| `GROQ_API_KEY` | If `LLM_PROVIDER=groq` | Free at https://console.groq.com |
| `OPENAI_API_KEY` | If `LLM_PROVIDER=openai` | |
| `ANTHROPIC_API_KEY` | If `LLM_PROVIDER=anthropic` | |
| `AZURE_OPENAI_API_KEY` | If `LLM_PROVIDER=azure` | Also needs `AZURE_OPENAI_ENDPOINT` |
| `MISTRAL_API_KEY` | If `LLM_PROVIDER=mistral` | |
| `GOOGLE_API_KEY` | If `LLM_PROVIDER=google` | |
| `OPENROUTER_API_KEY` | If `LLM_PROVIDER=openrouter` | |
| `TOGETHER_API_KEY` | If `LLM_PROVIDER=together` | |
| `OLLAMA_BASE_URL` | No | Default: `http://localhost:11434` |

Optional (for PR features):
- `GH_TOKEN` (GitHub Actions) or `gh auth login` (local) — required for `git-crew pr` and `--post-comment`.
