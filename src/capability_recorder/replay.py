"""
Deterministic replay engine.

Executes a recorded Capability step-by-step WITHOUT invoking an LLM,
using only the artifact's stored locators, risk classifications, and
pre-registered business-outcome/recovery signatures.

The actual mechanics of "how do I click this element" / "how do I read the
page" are deliberately kept behind the StepExecutor protocol below, so this
module can be tested with a fast, deterministic FakeExecutor before any real
browser (Playwright) dependency is wired in. This is the "seam" between
"how we perceive/act on a surface" and "the recorded flow" -- only
PlaywrightStepExecutor (added later) will know about real browser APIs;
everything in this file works against the abstract StepExecutor interface.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional, Protocol

from pydantic import BaseModel

from capability_recorder.recovery import find_matching_recovery_rule
from capability_recorder.schema import (
    BusinessOutcomeType,
    Capability,
    RiskLevel,
    Step,
)


# ---------------------------------------------------------------------------
# Executor interface -- the "seam"
# ---------------------------------------------------------------------------

class StepExecutor(Protocol):
    """Anything that can actually perform a Step and report what happened.

    A real implementation (e.g. PlaywrightStepExecutor) would use the
    step's ElementTarget locators to find and interact with real page
    elements. For now, FakeStepExecutor (in tests) lets us test replay's
    decision-making logic without a browser at all.
    """

    def execute(self, step: Step) -> "ExecutionResult":
        ...

    def observe_current_state(self) -> str:
        """Return a simple string signal describing what's currently on
        screen (e.g. page text) -- used for recovery-rule and
        business-outcome matching, which both operate on this same
        observed_state format (see recovery.py)."""
        ...


class ExecutionResult(BaseModel):
    """What actually happened when a step was executed, as reported by
    the executor -- BEFORE this module classifies it into the outcome
    taxonomy. `succeeded` reflects only whether the low-level action
    itself completed (e.g. the click landed), not whether the overall
    business goal was achieved."""
    succeeded: bool
    observed_state: str
    error_message: Optional[str] = None


# ---------------------------------------------------------------------------
# Outcome taxonomy
# ---------------------------------------------------------------------------

class OutcomeCategory(str, Enum):
    SUCCESS = "success"
    BUSINESS_OUTCOME = "business_outcome"
    RECOVERABLE = "recoverable"
    HARD_FAILURE = "hard_failure"
    ESCALATED = "escalated"   # risky step, paused for human confirmation


class StepOutcome(BaseModel):
    step_id: int
    category: OutcomeCategory
    business_outcome_type: Optional[BusinessOutcomeType] = None
    detail: str


class ReplayResult(BaseModel):
    capability_id: str
    final_category: OutcomeCategory
    step_outcomes: list[StepOutcome]
    detail: str


# ---------------------------------------------------------------------------
# Escalation hook -- kept abstract here on purpose (see escalation.py, later)
# ---------------------------------------------------------------------------

class EscalationHandler(Protocol):
    def escalate(self, step: Step, reason: str) -> bool:
        """Return True if a human approved/handled it and replay should
        continue; False if replay should stop."""
        ...


# ---------------------------------------------------------------------------
# The replay engine itself
# ---------------------------------------------------------------------------

def replay_capability(
    capability: Capability,
    executor: StepExecutor,
    escalation_handler: EscalationHandler,
) -> ReplayResult:
    step_outcomes: list[StepOutcome] = []

    for step in capability.steps:
        # 1. Risk gating -- checked BEFORE execution, independent of success/failure.
        if step.risk_level == RiskLevel.RISKY:
            approved = escalation_handler.escalate(step, reason="risky action requires confirmation")
            if not approved:
                outcome = StepOutcome(
                    step_id=step.step_id,
                    category=OutcomeCategory.ESCALATED,
                    detail="Escalated for risky action; human did not approve continuation.",
                )
                step_outcomes.append(outcome)
                return ReplayResult(
                    capability_id=capability.capability_id,
                    final_category=OutcomeCategory.ESCALATED,
                    step_outcomes=step_outcomes,
                    detail=f"Stopped at step {step.step_id}: risky action not approved.",
                )
            # NOTE: dynamic_risk_check (value-dependent escalation) is intentionally
            # not yet implemented here -- see REPORT.md "Cuts" section. Static
            # risk_level is enforced; per-invocation parameter thresholds are a
            # documented next step, not a silent gap.

        # 2. Execute the step via the injected executor.
        result = executor.execute(step)

        # 3. Classify the outcome.
        outcome = _classify_outcome(step, result, capability)
        step_outcomes.append(outcome)

        if outcome.category == OutcomeCategory.SUCCESS:
            continue

        if outcome.category == OutcomeCategory.BUSINESS_OUTCOME:
            return ReplayResult(
                capability_id=capability.capability_id,
                final_category=OutcomeCategory.BUSINESS_OUTCOME,
                step_outcomes=step_outcomes,
                detail=outcome.detail,
            )

        if outcome.category == OutcomeCategory.RECOVERABLE:
            continue

        if outcome.category == OutcomeCategory.HARD_FAILURE:
            approved = escalation_handler.escalate(step, reason="unrecoverable failure")
            if not approved:
                return ReplayResult(
                    capability_id=capability.capability_id,
                    final_category=OutcomeCategory.HARD_FAILURE,
                    step_outcomes=step_outcomes,
                    detail=outcome.detail,
                )
            continue

    return ReplayResult(
        capability_id=capability.capability_id,
        final_category=OutcomeCategory.SUCCESS,
        step_outcomes=step_outcomes,
        detail="All steps completed successfully.",
    )


def _classify_outcome(step: Step, result: ExecutionResult, capability: Capability) -> StepOutcome:
    if result.succeeded:
        return StepOutcome(
            step_id=step.step_id,
            category=OutcomeCategory.SUCCESS,
            detail="Step executed as expected.",
        )

    for expected in capability.expected_business_outcomes:
        if expected.after_step_id == step.step_id and _text_trigger_matches(expected.trigger, result.observed_state):
            return StepOutcome(
                step_id=step.step_id,
                category=OutcomeCategory.BUSINESS_OUTCOME,
                business_outcome_type=expected.outcome_type,
                detail=expected.description,
            )

    recovery_rule = find_matching_recovery_rule(result.observed_state, capability.local_recovery_rules)
    if recovery_rule is not None:
        return StepOutcome(
            step_id=step.step_id,
            category=OutcomeCategory.RECOVERABLE,
            detail=f"Matched recovery rule: {recovery_rule.recovery_action}",
        )

    return StepOutcome(
        step_id=step.step_id,
        category=OutcomeCategory.HARD_FAILURE,
        detail=result.error_message or f"Unrecognized state: {result.observed_state}",
    )


def _text_trigger_matches(trigger: str, observed_state: str) -> bool:
    if ":" not in trigger:
        return trigger in observed_state
    _prefix, payload = trigger.split(":", 1)
    return payload.lower() in observed_state.lower()