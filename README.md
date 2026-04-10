# git-crew

AI-powered git diff reviewer. Reviews only what changed, not the whole codebase.

Built with **LangGraph** (pipeline) + **CrewAI** (specialist crews) + **AutoGen** (interactive Q&A).

## How it works

```
your diff
    ↓
[LangGraph] classifies the diff, decides which reviews to run
    ↓
[CrewAI Security Crew]      3 agents: Auditor → Exploit Analyst → Reporter
[CrewAI Architecture Crew]  3 agents: Architect → Quality Reviewer → Reporter
[CrewAI Performance Crew]   3 agents: Profiler → Scalability Analyst → Reporter
    ↓
[LangGraph] assembles final report
    ↓
[AutoGen] interactive Q&A — ask questions about any finding
```

## Setup

```bash
pip install -r requirements.txt
export GROQ_API_KEY=your_key_here   # free at https://console.groq.com
```

## Usage

```bash
# Review staged changes (what you're about to commit)
git-crew review

# Review last 3 commits
git-crew review HEAD~3..HEAD

# Review a branch diff
git-crew review main..my-feature

# Review unstaged changes
git-crew review --unstaged

# Review a GitHub PR (requires gh CLI)
git-crew pr 42

# Post findings as a PR comment
git-crew pr 42 --post-comment

# Save report to a file
git-crew review -o report.md

# Non-interactive mode (for CI)
git-crew review --no-interactive

# Install as a git pre-push hook
git-crew install-hook
```

## What gets reviewed

| Crew | Finds |
|---|---|
| Security | SQL injection, hardcoded secrets, missing auth checks, unsafe deserialization |
| Architecture | SOLID violations, tight coupling, wrong abstractions, mixed concerns |
| Performance | Nested loops on collections, N+1 queries, expensive calls in hot paths |

**Diff-aware:** only changed lines are analyzed. Context lines are shown for readability but crews are instructed to ignore them.

**Smart routing:** docs-only diffs (`.md`, `.txt`) skip all crews. Files with security-related path names (auth, token, crypto, etc.) always trigger the Security Crew.

## Use as a GitHub Action

Automatically review every PR in your repository — no local setup required.

**Step 1** — Add your Groq API key as a repo secret named `GROQ_API_KEY`.
(Settings → Secrets and variables → Actions → New repository secret)

**Step 2** — Copy `workflow-template.yml` from this repo to `.github/workflows/git-crew-review.yml` in your repo. Replace `YOUR_GITHUB_USERNAME` with the actual owner:

```yaml
- uses: YOUR_GITHUB_USERNAME/git-crew@main
  with:
    groq-api-key: ${{ secrets.GROQ_API_KEY }}
```

That's it. Every PR open/update will trigger a security + architecture + performance review posted as a PR comment.

**Permissions required:** `pull-requests: write` (already in the template).
**No `GITHUB_TOKEN` secret needed** — the built-in token is used automatically.

---

## Requirements

- Python 3.11+
- `GROQ_API_KEY` — free at https://console.groq.com
- `gh` CLI (only for `git-crew pr` and `--post-comment`) — https://cli.github.com
