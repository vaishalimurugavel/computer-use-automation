"""
Tests for the replay engine.

Uses FakeStepExecutor and FakeEscalationHandler (defined below) instead of
real Playwright, so these tests are fast, deterministic, and exercise only
the decision-making logic: risk gating, outcome classification, and
control flow for each of the four outcome categories.
"""

import pytest

from capability_recorder.replay import (
    ExecutionResult,
    OutcomeCategory,
    replay_capability,
)
from capability_recorder.schema import (
    ActionType,
    BusinessOutcomeType,
    Capability,
    ElementTarget,
    ExpectedBusinessOutcome,
    Locator,
    LocatorStrategy,
    RecoverableConditionType,
    RecoveryRule,
    RiskLevel,
    Step,
    SuccessCondition,
)


class FakeStepExecutor:
    """Scripted executor: returns a pre-programmed ExecutionResult per
    step_id, in the order provided. Lets each test control exactly what
    "happens" at each step without any real browser interaction."""

    def __init__(self, results_by_step_id: dict[int, ExecutionResult], final_state: str = "ok"):
        self._results = results_by_step_id
        self._final_state = final_state

    def execute(self, step: Step) -> ExecutionResult:
        return self._results[step.step_id]

    def observe_current_state(self) -> str:
        return self._final_state


class FakeEscalationHandler:
    """Records every escalation call it receives and returns a
    pre-configured approval decision, so tests can assert both that
    escalation was triggered AND how many times."""

    def __init__(self, approve: bool = True):
        self.approve = approve
        self.calls: list[tuple[int, str]] = []

    def escalate(self, step: Step, reason: str) -> bool:
        self.calls.append((step.step_id, reason))
        return self.approve


def _success_condition() -> SuccessCondition:
    return SuccessCondition(
        description="dummy",
        check=ElementTarget(description="dummy", locators=[
            Locator(strategy=LocatorStrategy.TEXT_CONTENT, value="ok", confidence=0.9)
        ]),
    )


def _capability(steps: list[Step], **kwargs) -> Capability:
    kwargs.setdefault("success_condition", _success_condition())
    return Capability(
        capability_id="cap-test",
        name="test_capability",
        version=1,
        target_app="test_app",
        description="test",
        steps=steps,
        **kwargs,
    )

def test_all_steps_succeed_returns_success():
    cap = _capability([
        Step(step_id=1, action=ActionType.NAVIGATE, value="https://example.com"),
        Step(step_id=2, action=ActionType.CLICK),
    ])
    executor = FakeStepExecutor({
        1: ExecutionResult(succeeded=True, observed_state="page loaded"),
        2: ExecutionResult(succeeded=True, observed_state="button clicked"),
    })
    escalation = FakeEscalationHandler()

    result = replay_capability(cap, executor, escalation)

    assert result.final_category == OutcomeCategory.SUCCESS
    assert len(result.step_outcomes) == 2
    assert escalation.calls == []


def test_risky_step_triggers_escalation_before_execution():
    cap = _capability([
        Step(step_id=1, action=ActionType.CLICK, risk_level=RiskLevel.RISKY),
    ])
    executor = FakeStepExecutor({
        1: ExecutionResult(succeeded=True, observed_state="done"),
    })
    escalation = FakeEscalationHandler(approve=True)

    result = replay_capability(cap, executor, escalation)

    assert escalation.calls == [(1, "risky action requires confirmation")]
    assert result.final_category == OutcomeCategory.SUCCESS


def test_risky_step_not_approved_stops_replay_without_executing():
    cap = _capability([
        Step(step_id=1, action=ActionType.CLICK, risk_level=RiskLevel.RISKY),
        Step(step_id=2, action=ActionType.CLICK),
    ])
    executor = FakeStepExecutor({
        1: ExecutionResult(succeeded=True, observed_state="should not be reached"),
        2: ExecutionResult(succeeded=True, observed_state="should not be reached"),
    })
    escalation = FakeEscalationHandler(approve=False)

    result = replay_capability(cap, executor, escalation)

    assert result.final_category == OutcomeCategory.ESCALATED
    assert len(result.step_outcomes) == 1


def test_pre_registered_business_outcome_is_recognized_not_treated_as_failure():
    cap = _capability(
        [Step(step_id=1, action=ActionType.CLICK)],
        expected_business_outcomes=[
            ExpectedBusinessOutcome(
                after_step_id=1,
                trigger="text_contains:No member found",
                outcome_type=BusinessOutcomeType.NOT_FOUND,
                description="Member does not exist",
            )
        ],
    )
    executor = FakeStepExecutor({
        1: ExecutionResult(succeeded=False, observed_state="Error: No member found for this ID"),
    })
    escalation = FakeEscalationHandler()

    result = replay_capability(cap, executor, escalation)

    assert result.final_category == OutcomeCategory.BUSINESS_OUTCOME
    assert result.step_outcomes[0].business_outcome_type == BusinessOutcomeType.NOT_FOUND
    assert escalation.calls == []


def test_global_recoverable_condition_allows_replay_to_continue():
    cap = _capability([
        Step(step_id=1, action=ActionType.CLICK),
        Step(step_id=2, action=ActionType.CLICK),
    ])
    executor = FakeStepExecutor({
        1: ExecutionResult(succeeded=False, observed_state="Your session has expired."),
        2: ExecutionResult(succeeded=True, observed_state="done"),
    })
    escalation = FakeEscalationHandler()

    result = replay_capability(cap, executor, escalation)

    assert result.step_outcomes[0].category == OutcomeCategory.RECOVERABLE
    assert result.final_category == OutcomeCategory.SUCCESS
    assert escalation.calls == []


def test_local_recovery_rule_is_honored_when_no_global_rule_matches():
    cap = _capability(
        [Step(step_id=1, action=ActionType.CLICK), Step(step_id=2, action=ActionType.CLICK)],
        local_recovery_rules=[
            RecoveryRule(
                trigger="text_contains:duplicate account warning",
                condition_type=RecoverableConditionType.KNOWN_DIALOG_DISMISS,
                recovery_action="dismiss_dialog",
            )
        ],
    )
    executor = FakeStepExecutor({
        1: ExecutionResult(succeeded=False, observed_state="duplicate account warning shown"),
        2: ExecutionResult(succeeded=True, observed_state="done"),
    })
    escalation = FakeEscalationHandler()

    result = replay_capability(cap, executor, escalation)

    assert result.step_outcomes[0].category == OutcomeCategory.RECOVERABLE
    assert result.final_category == OutcomeCategory.SUCCESS


def test_unrecognized_failure_escalates_as_hard_failure_by_default():
    cap = _capability([
        Step(step_id=1, action=ActionType.CLICK),
    ])
    executor = FakeStepExecutor({
        1: ExecutionResult(succeeded=False, observed_state="Completely unrecognized page state"),
    })
    escalation = FakeEscalationHandler(approve=False)

    result = replay_capability(cap, executor, escalation)

    assert result.step_outcomes[0].category == OutcomeCategory.HARD_FAILURE
    assert escalation.calls == [(1, "unrecoverable failure")]
    assert result.final_category == OutcomeCategory.HARD_FAILURE


def test_hard_failure_resolved_by_human_allows_replay_to_continue():
    cap = _capability([
        Step(step_id=1, action=ActionType.CLICK),
        Step(step_id=2, action=ActionType.CLICK),
    ])
    executor = FakeStepExecutor({
        1: ExecutionResult(succeeded=False, observed_state="Completely unrecognized page state"),
        2: ExecutionResult(succeeded=True, observed_state="done"),
    })
    escalation = FakeEscalationHandler(approve=True)

    result = replay_capability(cap, executor, escalation)

    assert result.final_category == OutcomeCategory.SUCCESS
    assert len(result.step_outcomes) == 2


def test_placeholder_value_is_substituted_with_supplied_input_value():
    cap = _capability([
        Step(step_id=1, action=ActionType.TYPE, value="{{password}}"),
    ])
    executor = FakeStepExecutor({
        1: ExecutionResult(succeeded=True, observed_state="typed"),
    })
    escalation = FakeEscalationHandler()

    result = replay_capability(cap, executor, escalation, input_values={"password": "real_secret"})

    assert result.final_category == OutcomeCategory.SUCCESS


def test_missing_input_value_for_placeholder_raises_rather_than_using_blank():
    from capability_recorder.replay import MissingInputValueError

    cap = _capability([
        Step(step_id=1, action=ActionType.TYPE, value="{{password}}"),
    ])
    executor = FakeStepExecutor({
        1: ExecutionResult(succeeded=True, observed_state="typed"),
    })
    escalation = FakeEscalationHandler()

    with pytest.raises(MissingInputValueError):
        replay_capability(cap, executor, escalation, input_values={})


def test_non_placeholder_values_are_passed_through_unchanged():
    cap = _capability([
        Step(step_id=1, action=ActionType.TYPE, value="standard_user"),
    ])
    executor = FakeStepExecutor({
        1: ExecutionResult(succeeded=True, observed_state="typed"),
    })
    escalation = FakeEscalationHandler()

    result = replay_capability(cap, executor, escalation, input_values={})

    assert result.final_category == OutcomeCategory.SUCCESS


def test_success_condition_met_reports_success_with_no_escalation():
    cap = _capability([
        Step(step_id=1, action=ActionType.CLICK),
    ])
    executor = FakeStepExecutor(
        {1: ExecutionResult(succeeded=True, observed_state="clicked")},
        final_state="Your order is ok and confirmed",
    )
    escalation = FakeEscalationHandler()

    result = replay_capability(cap, executor, escalation)

    assert result.final_category == OutcomeCategory.SUCCESS
    assert escalation.calls == []


def test_success_condition_not_met_escalates_and_reports_hard_failure_if_denied():
    cap = _capability([
        Step(step_id=1, action=ActionType.CLICK),
    ])
    executor = FakeStepExecutor(
        {1: ExecutionResult(succeeded=True, observed_state="clicked")},
        final_state="Something completely different, no match here",
    )
    escalation = FakeEscalationHandler(approve=False)

    result = replay_capability(cap, executor, escalation)

    assert result.final_category == OutcomeCategory.HARD_FAILURE
    assert len(escalation.calls) == 1
    assert "success_condition" in escalation.calls[0][1]  # underscore, matching replay.py's actual wording


def test_success_condition_not_met_but_human_approves_still_reports_success():
    cap = _capability([
        Step(step_id=1, action=ActionType.CLICK),
    ])
    executor = FakeStepExecutor(
        {1: ExecutionResult(succeeded=True, observed_state="clicked")},
        final_state="Different wording entirely",
    )
    escalation = FakeEscalationHandler(approve=True)

    result = replay_capability(cap, executor, escalation)

    assert result.final_category == OutcomeCategory.SUCCESS
    assert any(o.category == OutcomeCategory.HARD_FAILURE for o in result.step_outcomes)


def test_non_text_content_success_condition_is_treated_as_unverifiable():
    cap = _capability(
        [Step(step_id=1, action=ActionType.CLICK)],
        success_condition=SuccessCondition(
            description="role-based check",
            check=ElementTarget(
                description="dummy",
                locators=[Locator(strategy=LocatorStrategy.ACCESSIBILITY_ROLE, value="role:button;name:Confirmed", confidence=0.9)],
            ),
        ),
    )
    executor = FakeStepExecutor(
        {1: ExecutionResult(succeeded=True, observed_state="clicked")},
        final_state="anything at all",
    )
    escalation = FakeEscalationHandler(approve=False)

    result = replay_capability(cap, executor, escalation)

    assert result.final_category == OutcomeCategory.HARD_FAILURE


# ---------------------------------------------------------------------------
# Allowlist enforcement (real gap fix)
# ---------------------------------------------------------------------------

def test_step_within_allowlist_executes_normally():
    from capability_recorder.safety import Allowlist

    cap = _capability([
        Step(step_id=1, action=ActionType.NAVIGATE, value="https://www.saucedemo.com/"),
    ])
    executor = FakeStepExecutor({
        1: ExecutionResult(succeeded=True, observed_state="ok"),
    })
    escalation = FakeEscalationHandler()
    allowlist = Allowlist(allowed_domains=["saucedemo.com"])

    result = replay_capability(cap, executor, escalation, allowlist=allowlist)

    assert result.final_category == OutcomeCategory.SUCCESS
    assert escalation.calls == []


def test_navigation_outside_allowlist_is_blocked_before_execution():
    from capability_recorder.safety import Allowlist

    cap = _capability([
        Step(step_id=1, action=ActionType.NAVIGATE, value="https://evil-example.com/"),
        Step(step_id=2, action=ActionType.CLICK),
    ])
    executor = FakeStepExecutor({
        1: ExecutionResult(succeeded=True, observed_state="should never be seen -- execute() should not be called"),
        2: ExecutionResult(succeeded=True, observed_state="should never be reached"),
    })
    escalation = FakeEscalationHandler(approve=False)
    allowlist = Allowlist(allowed_domains=["saucedemo.com"])

    result = replay_capability(cap, executor, escalation, allowlist=allowlist)

    assert result.final_category == OutcomeCategory.HARD_FAILURE
    assert "Allowlist violation" in result.step_outcomes[0].detail
    assert len(result.step_outcomes) == 1


def test_allowlist_violation_can_be_overridden_by_explicit_human_approval():
    from capability_recorder.safety import Allowlist

    cap = _capability([
        Step(step_id=1, action=ActionType.NAVIGATE, value="https://evil-example.com/"),
    ])
    executor = FakeStepExecutor({
        1: ExecutionResult(succeeded=True, observed_state="ok"),
    })
    escalation = FakeEscalationHandler(approve=True)
    allowlist = Allowlist(allowed_domains=["saucedemo.com"])

    result = replay_capability(cap, executor, escalation, allowlist=allowlist)

    assert len(escalation.calls) == 1
    assert "allowlist violation" in escalation.calls[0][1]
    assert result.final_category == OutcomeCategory.SUCCESS


def test_no_allowlist_supplied_means_no_policy_check_at_all():
    cap = _capability([
        Step(step_id=1, action=ActionType.NAVIGATE, value="https://anything-at-all.com/"),
    ])
    executor = FakeStepExecutor({
        1: ExecutionResult(succeeded=True, observed_state="ok"),
    })
    escalation = FakeEscalationHandler()

    result = replay_capability(cap, executor, escalation, allowlist=None)

    assert result.final_category == OutcomeCategory.SUCCESS
    assert escalation.calls == []