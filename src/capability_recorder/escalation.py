"""
Human escalation handlers, implementing the EscalationHandler protocol
(see replay.py): escalate(step, reason) -> bool.

Three implementations:
- ConsoleEscalationHandler: a real, interactive handler -- prints the
  step's details and the reason it was escalated, then prompts a human
  for a simple approve/deny decision. Good for a quick CLI demo.
- LiveSessionEscalationHandler: the fuller model the assignment
  describes -- lets a human take over the SAME live browser session the
  automation was using, act manually if needed, then hand control back.
  See its own docstring for the mechanism.
- NullEscalationHandler: a fail-safe default that always denies, with no
  interaction at all. Intended for headless/automated contexts where no
  human is available to ask -- denying by default is consistent with the
  "never silently guess" principle applied throughout this project
  (see recovery.py, replay.py's HARD_FAILURE default, llm_client.py's
  parse error handling): if there's no way to get a real answer, the
  safe default is to NOT proceed, not to assume approval.
"""

from __future__ import annotations

from typing import Callable

from capability_recorder.schema import Step


class ConsoleEscalationHandler:
    """Prompts a human via the console for approval. The input function
    is injectable (defaults to the built-in input()) specifically so this
    can be unit tested without needing to mock builtins directly.
    """

    def __init__(self, input_func: Callable[[str], str] = input):
        self._input = input_func

    def escalate(self, step: Step, reason: str) -> bool:
        print("\n" + "=" * 60)
        print("HUMAN APPROVAL REQUIRED")
        print("=" * 60)
        print(f"Step {step.step_id}: {step.action}")
        if step.target is not None:
            print(f"Target: {step.target.description}")
        if step.value is not None:
            print(f"Value: {step.value}")
        print(f"Reason for escalation: {reason}")
        print("=" * 60)

        response = self._input("Approve this action? [y/N]: ").strip().lower()
        approved = response in ("y", "yes")

        print("Approved -- continuing." if approved else "Denied -- stopping.")
        return approved


class LiveSessionEscalationHandler:
    """Lets a human take over the SAME live browser session the
    automation was using, act manually if needed, then hand control back
    -- rather than a plain approve/deny prompt. This is the fuller model
    the assignment describes for escalation & handoff (see REPORT.md).

    Mechanism: since this handler is constructed with a reference to the
    same live `page` object the StepExecutor is driving, the human can
    literally interact with that exact, already-open, already-
    authenticated browser window while replay is paused at input().
    There is no new session, no re-login, no lost state -- the human is
    acting in the same context the automation was just in.

    `page` only needs a `.url` property for this handler to report where
    things stand; it's typed loosely (not as playwright.sync_api.Page)
    so this can be unit tested with a simple fake object.
    """

    def __init__(self, page, input_func: Callable[[str], str] = input):
        self._page = page
        self._input = input_func

    def escalate(self, step: Step, reason: str) -> bool:
        print("\n" + "=" * 60)
        print("HUMAN HANDOFF -- LIVE SESSION")
        print("=" * 60)
        print(f"Step {step.step_id}: {step.action}")
        if step.target is not None:
            print(f"Target: {step.target.description}")
        print(f"Reason for escalation: {reason}")
        print(f"Current page: {self._page.url}")
        print("-" * 60)
        print("The browser window is still open and live.")
        print("You may interact with it directly right now if needed.")
        print("-" * 60)

        response = self._input(
            "Press Enter once ready to resume replay on this session, or type 'n' to abort: "
        ).strip().lower()
        resume = response not in ("n", "no")

        print("Resuming replay on the same session." if resume else "Aborting replay.")
        return resume


class NullEscalationHandler:
    """Fail-safe default: always denies, without prompting anyone. Use
    this in headless/automated contexts where no human is available to
    respond -- e.g. a scheduled batch replay run with no one watching."""

    def escalate(self, step: Step, reason: str) -> bool:
        return False