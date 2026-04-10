"""
interactive.py — AutoGen post-review Q&A session

After the CrewAI crews produce their findings, the developer can ask follow-up
questions in a live conversation. This is where AutoGen earns its place:
  - Static report: CrewAI Reporter produced it
  - Follow-up conversation: AutoGen drives it

Two-agent setup:
  ReviewerAgent   (AssistantAgent) — loaded with the full report + diff as context.
                                     Answers questions, explains findings, suggests fixes.
  DeveloperProxy  (UserProxy)      — represents the developer; human_input_mode="ALWAYS"
                                     so the developer types real questions.

Termination:
  The conversation ends when the developer types "exit", "done", "quit", or "bye",
  or when max_turns is reached (default 20).

Why AutoGen (not CrewAI or LangGraph) for this:
  CrewAI runs scripted task sequences — no real-time user input.
  LangGraph is great for automated pipelines — not for interactive REPL sessions.
  AutoGen's UserProxy + AssistantAgent with human_input_mode="ALWAYS" is the exact
  pattern designed for this: a human and an AI talking back and forth.
"""

import autogen

from .llm import get_autogen_config

_TERMINATION_PHRASES = {"exit", "done", "quit", "bye", "q", "no", "thanks"}


def _is_termination(msg: dict) -> bool:
    """Return True if the developer's last message signals they want to end."""
    content = (msg.get("content") or "").strip().lower().rstrip("!.")
    return content in _TERMINATION_PHRASES


def start_interactive_session(
    final_report: str,
    diff_text: str,
    max_turns: int = 20,
) -> None:
    """
    Start an interactive AutoGen Q&A session after the review completes.

    The ReviewerAgent is pre-loaded with:
      - The complete review report (all three crews' findings)
      - The raw diff (so it can reference specific lines)

    The developer can ask questions like:
      "Why is line 47 a SQL injection risk?"
      "Can you show me a fixed version of the auth function?"
      "What's the highest priority fix?"
      "Explain the architecture issue in auth.py"

    Args:
        final_report:   The assembled Markdown report from the aggregate node
        diff_text:      Raw git diff text (for the ReviewerAgent's context)
        max_turns:      Maximum conversation turns before auto-terminating
    """
    llm_config = get_autogen_config()

    system_message = f"""You are a senior code reviewer who just finished analyzing a git diff.
You have the full review report and the original diff available to you.

REVIEW REPORT:
{final_report}

ORIGINAL DIFF (for reference):
{diff_text[:8000]}{"[...diff truncated...]" if len(diff_text) > 8000 else ""}

Your job now is to answer the developer's follow-up questions:
- Explain any finding in more detail
- Show fixed versions of problematic code
- Prioritize what to fix first
- Discuss trade-offs of different solutions
- Confirm whether a proposed fix is correct

Be specific. Reference exact file names and line numbers. If asked for a fix,
provide actual corrected code, not just a description.

When the developer says "exit", "done", "quit", "bye", or similar, end the session.
"""

    reviewer_agent = autogen.AssistantAgent(
        name="ReviewerAgent",
        system_message=system_message,
        llm_config=llm_config,
        is_termination_msg=_is_termination,
    )

    developer_proxy = autogen.UserProxyAgent(
        name="DeveloperProxy",
        human_input_mode="ALWAYS",         # developer types every message
        max_consecutive_auto_reply=0,       # never auto-reply — always ask human
        is_termination_msg=_is_termination,
        code_execution_config=False,        # don't execute code in this session
    )

    print("\n" + "─" * 60)
    print("  Interactive Review Session")
    print("  Ask questions about any finding. Type 'exit' to quit.")
    print("─" * 60 + "\n")

    developer_proxy.initiate_chat(
        reviewer_agent,
        message=(
            "I've just reviewed your code diff. I found the issues listed in the report above. "
            "What would you like to know more about? You can ask me to explain any finding, "
            "show you a fix, or help you prioritize what to address first."
        ),
        max_turns=max_turns,
    )

    print("\n" + "─" * 60)
    print("  Session ended.")
    print("─" * 60 + "\n")
