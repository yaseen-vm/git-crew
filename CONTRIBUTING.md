# Contributing to git-crew

## Development setup

```bash
git clone https://github.com/yaseen-vm/git-crew.git
cd git-crew
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env   # add your GROQ_API_KEY
```

## Linting

```bash
ruff check gitcrew/
ruff format gitcrew/
```

## Running a review against your own changes

```bash
git-crew review HEAD~1..HEAD
```

---

## Adding a new crew

The project follows a strict 3-agent pattern for every crew. To add, say, a **Test Coverage Crew**:

**1.** Create `gitcrew/crews/test_crew.py` — copy the structure from an existing crew.

Three agents: `Analyst → Domain Expert → Reporter`. Use `context=[prev_task]` to chain outputs.

**2.** Add the state field in `orchestrator.py`:
```python
class ReviewState(TypedDict):
    ...
    test_findings: str          # add this
    run_tests: bool             # add this
```

**3.** Import and call in the `run_all_crews` node (the parallel executor):
```python
if state["run_tests"]:
    tasks["test"] = executor.submit(run_test_crew, state["formatted_diff"])
```

**4.** Add routing logic in the `classify` node:
```python
run_tests = not is_docs_only
```

**5.** Include findings in the `aggregate` node's report template.

**6.** Add a section to the README table.

---

## Project structure

```
gitcrew/
  git.py            — diff parsing, no LLM calls
  orchestrator.py   — LangGraph pipeline, parallel crew execution
  crews/
    security_crew.py
    architecture_crew.py
    performance_crew.py
  interactive.py    — AutoGen Q&A session
  report.py         — Rich output, SARIF export, PR comments
  cli.py            — Typer CLI
```

## Commit style

Plain imperative subject line, no AI attribution.

```
Add test coverage crew
Fix SARIF severity mapping for CRITICAL findings
```

## Opening a PR

Use the PR template. Link any related issue. Include a sample report output in the description if you changed crew prompts.
