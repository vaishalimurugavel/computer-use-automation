"""
Tests for the core artifact schema.

These tests focus on the design decisions we reasoned through explicitly:
- Locator.reasoning is optional and doesn't break construction either way
- Step.risk_level defaults to SAFE, requiring explicit opt-in to RISKY
- Capability requires the mandatory pieces (steps, success_condition) but
  tolerates empty lists for the optional/extensible collections
- Confidence is bounded to [0.0, 1.0]
"""

import pytest
from pydantic import ValidationError

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


# ---------------------------------------------------------------------------
# Locator / ElementTarget
# ---------------------------------------------------------------------------

def test_locator_reasoning_is_optional():
    loc_without_reasoning = Locator(
        strategy=LocatorStrategy.CSS_SELECTOR,
        value="#add-to-cart",
        confidence=0.9,
    )
    assert loc_without_reasoning.reasoning is None

    loc_with_reasoning = Locator(
        strategy=LocatorStrategy.CSS_SELECTOR,
        value="#add-to-cart",
        confidence=0.6,
        reasoning="no accessible name present; falling back to CSS selector",
    )
    assert loc_with_reasoning.reasoning is not None


def test_locator_confidence_must_be_between_zero_and_one():
    with pytest.raises(ValidationError):
        Locator(strategy=LocatorStrategy.CSS_SELECTOR, value="#x", confidence=1.5)

    with pytest.raises(ValidationError):
        Locator(strategy=LocatorStrategy.CSS_SELECTOR, value="#x", confidence=-0.1)


def test_element_target_preserves_locator_order():
    """Locator order encodes fallback preference -- accessibility first,
    then CSS, matching the hybrid strategy we designed."""
    target = ElementTarget(
        description="Add to Cart button",
        locators=[
            Locator(strategy=LocatorStrategy.ACCESSIBILITY_ROLE, value="button:Add to Cart", confidence=0.95),
            Locator(strategy=LocatorStrategy.CSS_SELECTOR, value="#add-to-cart", confidence=0.7),
        ],
    )
    assert target.locators[0].strategy == LocatorStrategy.ACCESSIBILITY_ROLE
    assert target.locators[1].strategy == LocatorStrategy.CSS_SELECTOR


# ---------------------------------------------------------------------------
# Step / risk model
# ---------------------------------------------------------------------------

def test_step_risk_level_defaults_to_safe():
    step = Step(step_id=1, action=ActionType.CLICK)
    assert step.risk_level == RiskLevel.SAFE


def test_step_can_be_explicitly_marked_risky():
    step = Step(step_id=5, action=ActionType.CLICK, risk_level=RiskLevel.RISKY)
    assert step.risk_level == RiskLevel.RISKY


def test_step_dynamic_risk_check_is_independent_of_static_risk_level():
    """A step can be statically SAFE but still carry a dynamic check that
    can upgrade risk for a specific invocation based on real parameter values."""
    step = Step(
        step_id=3,
        action=ActionType.TYPE,
        risk_level=RiskLevel.SAFE,
        dynamic_risk_check="amount > 1000",
    )
    assert step.risk_level == RiskLevel.SAFE
    assert step.dynamic_risk_check == "amount > 1000"


def test_navigate_step_has_no_target():
    step = Step(step_id=1, action=ActionType.NAVIGATE, value="https://example.com")
    assert step.target is None


# ---------------------------------------------------------------------------
# Capability (the full artifact)
# ---------------------------------------------------------------------------

def _minimal_success_condition() -> SuccessCondition:
    return SuccessCondition(
        description="Confirmation banner visible",
        check=ElementTarget(
            description="Order confirmation banner",
            locators=[
                Locator(strategy=LocatorStrategy.TEXT_CONTENT, value="Thank you for your order", confidence=0.85),
            ],
        ),
    )


def test_capability_requires_at_least_the_mandatory_fields():
    cap = Capability(
        capability_id="cap-001",
        name="add_item_to_cart_and_checkout",
        version=1,
        target_app="saucedemo",
        description="Adds a named item to cart and reaches checkout confirmation",
        steps=[Step(step_id=1, action=ActionType.NAVIGATE, value="https://saucedemo.com")],
        success_condition=_minimal_success_condition(),
    )
    assert cap.version == 1
    assert cap.input_params == []
    assert cap.expected_business_outcomes == []
    assert cap.local_recovery_rules == []
    assert cap.revision_history == []


def test_capability_rejects_missing_required_fields():
    with pytest.raises(ValidationError):
        Capability(
            capability_id="cap-002",
            name="incomplete_capability",
            version=1,
            target_app="saucedemo",
            description="missing steps and success_condition",
        )  # type: ignore[call-arg]


def test_capability_can_carry_expected_business_outcomes():
    cap = Capability(
        capability_id="cap-003",
        name="search_member",
        version=1,
        target_app="internal_crm",
        description="Search for a member by ID",
        steps=[Step(step_id=1, action=ActionType.NAVIGATE, value="https://crm.example.com")],
        success_condition=_minimal_success_condition(),
        expected_business_outcomes=[
            ExpectedBusinessOutcome(
                after_step_id=1,
                trigger="text_contains:No member found",
                outcome_type=BusinessOutcomeType.NOT_FOUND,
                description="Member ID does not exist in the system",
            )
        ],
    )
    assert len(cap.expected_business_outcomes) == 1
    assert cap.expected_business_outcomes[0].outcome_type == BusinessOutcomeType.NOT_FOUND


def test_capability_local_recovery_rules_are_scoped_to_this_artifact_only():
    """Local recovery rules live on the Capability itself; global rules
    (see recovery.py) are intentionally NOT part of this schema, since they
    are a property of the target application, not of any one recorded task."""
    cap = Capability(
        capability_id="cap-004",
        name="open_sub_account",
        version=1,
        target_app="internal_crm",
        description="Open a new sub-account for a member",
        steps=[Step(step_id=1, action=ActionType.CLICK, risk_level=RiskLevel.RISKY)],
        success_condition=_minimal_success_condition(),
        local_recovery_rules=[
            RecoveryRule(
                trigger="text_contains:duplicate account warning",
                condition_type=RecoverableConditionType.KNOWN_DIALOG_DISMISS,
                recovery_action="dismiss_dialog",
            )
        ],
    )
    assert len(cap.local_recovery_rules) == 1
    assert cap.local_recovery_rules[0].condition_type == RecoverableConditionType.KNOWN_DIALOG_DISMISS