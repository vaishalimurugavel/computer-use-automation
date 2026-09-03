"""
Human escalation handlers, implementing the EscalationHandler protocol
(see replay.py): escalate(step, reason) -> bool.

Two implementations:
- ConsoleEscalationHandler: a real, interactive handler -- prints the
  step's details and the reason it was escalated, then prompts a human
  for approval. This is the "real" implementation for a CLI-driven demo,
  in the same spirit as PlaywrightStepExecutor being the real
  implementation of StepExecutor.
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


class NullEscalationHandler:
    """Fail-safe default: always denies, without prompting anyone. Use
    this in headless/automated contexts where no human is available to
    respond -- e.g. a scheduled batch replay run with no one watching."""

    def escalate(self, step: Step, reason: str) -> bool:
        return False