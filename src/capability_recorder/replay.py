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

import re

from enum import Enum
from typing import Optional, Protocol

from pydantic import BaseModel

from capability_recorder.recovery import find_matching_recovery_rule
from capability_recorder.schema import (
    ActionType,
    BusinessOutcomeType,
    Capability,
    LocatorStrategy,
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

_PLACEHOLDER_PATTERN = re.compile(r"^\{\{(\w+)\}\}$")


class MissingInputValueError(Exception):
    """Raised when a step references a redacted parameter (e.g.
    '{{password}}') but no value was supplied for it. Never falls back to
    a blank or guessed value -- consistent with the project-wide 'never
    silently guess' principle."""


def _resolve_step_value(step: Step, input_values: dict[str, str]) -> Step:
    """If step.value is a placeholder like '{{password}}' (see
    agent.py::decision_to_step, which redacts sensitive field values into
    such placeholders at recording time), substitute the real value
    supplied for this replay. Otherwise return the step unchanged.

    This is what lets a Capability artifact be safely persisted without
    ever containing a real secret, while still being replayable: the real
    value is supplied fresh, per invocation, and never written to disk.
    """
    if step.value is None:
        return step
    match = _PLACEHOLDER_PATTERN.match(step.value)
    if not match:
        return step
    param_name = match.group(1)
    if param_name not in input_values:
        raise MissingInputValueError(
            f"Step {step.step_id} requires input parameter {param_name!r}, "
            f"which was not supplied in input_values."
        )
    return step.model_copy(update={"value": input_values[param_name]})


def _success_condition_met(capability: Capability, observed_state: str) -> bool:
    """Check the capability's success_condition against the final page
    state, once all steps have executed. Currently only TEXT_CONTENT
    locator strategies are checkable against a plain-text observed_state
    (the same format used for business-outcome and recovery-rule
    matching elsewhere in this module). Other strategies (accessibility
    role, CSS selector) aren't verifiable from text alone -- treated
    conservatively as "cannot verify, assume not met" rather than
    silently passing, consistent with the project-wide 'never guess'
    principle.
    """
    locator = capability.success_condition.check.locators[0]
    if locator.strategy == LocatorStrategy.TEXT_CONTENT:
        return locator.value.lower() in observed_state.lower()
    return False


def replay_capability(
    capability: Capability,
    executor: StepExecutor,
    escalation_handler: EscalationHandler,
    input_values: dict[str, str] | None = None,
) -> ReplayResult:
    input_values = input_values or {}
    step_outcomes: list[StepOutcome] = []

    for step in capability.steps:
        step = _resolve_step_value(step, input_values)

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

    # All steps executed without a stopping failure -- but that only means
    # each individual action completed. Verify the actual goal was reached
    # by checking success_condition against the final observed state,
    # rather than assuming success purely because nothing errored.
    final_state = executor.observe_current_state()
    if _success_condition_met(capability, final_state):
        return ReplayResult(
            capability_id=capability.capability_id,
            final_category=OutcomeCategory.SUCCESS,
            step_outcomes=step_outcomes,
            detail="All steps completed successfully and success_condition was verified.",
        )

    # Steps completed, but the declared success_condition was NOT
    # observed -- this is a real discrepancy worth escalating, not a
    # silent pass. E.g. every click/type succeeded, but the expected
    # confirmation text never appeared.
    verification_step = Step(
        step_id=capability.steps[-1].step_id + 1 if capability.steps else 1,
        action=ActionType.EXTRACT,
    )
    verification_outcome = StepOutcome(
        step_id=verification_step.step_id,
        category=OutcomeCategory.HARD_FAILURE,
        detail=f"success_condition not verified: {capability.success_condition.description}",
    )
    step_outcomes.append(verification_outcome)

    approved = escalation_handler.escalate(
        verification_step,
        reason="all steps executed, but the declared success_condition could not be verified against the final page state",
    )
    if not approved:
        return ReplayResult(
            capability_id=capability.capability_id,
            final_category=OutcomeCategory.HARD_FAILURE,
            step_outcomes=step_outcomes,
            detail=verification_outcome.detail,
        )

    # A human reviewed the discrepancy and confirmed it's fine (e.g. the
    # confirmation text changed wording since recording) -- proceed as
    # success, but the fact that this required human confirmation is
    # preserved in step_outcomes for anyone reviewing the run later.
    return ReplayResult(
        capability_id=capability.capability_id,
        final_category=OutcomeCategory.SUCCESS,
        step_outcomes=step_outcomes,
        detail="All steps completed; success_condition mismatch was reviewed and approved by a human.",
    )


def _classify_outcome(step: Step, result: ExecutionResult, capability: Capability) -> StepOutcome:
    """Decide which of the four outcome categories this step's execution
    falls into. Order of checks matters: business outcomes are checked
    before generic recovery, since a pre-registered business outcome is a
    more specific, more confident match than a generic recovery trigger.
    """
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