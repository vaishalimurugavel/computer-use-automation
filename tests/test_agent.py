"""
Tests for agent.py's pure logic: parse_locator_value and decision_to_step.

discover_capability() and _execute_on_page() are NOT tested here -- they
require a live Playwright page and a real LLM call. These tests cover
the translation logic between an LLM's decision and recorded/execution
steps, which is where real bugs (wrong index, malformed locator, and --
found via a live run -- executing the REDACTED value instead of the
real one) would surface.
"""

import pytest

from capability_recorder.agent import decision_to_step, parse_locator_value
from capability_recorder.llm_client import AgentDecision
from capability_recorder.perception import Observation, ObservedElement
from capability_recorder.schema import Locator, LocatorStrategy


def _observation() -> Observation:
    return Observation(
        url="https://www.saucedemo.com/inventory.html",
        page_text="Products",
        elements=[
            ObservedElement(
                index=1, role="button", name="Add to cart",
                locators=[Locator(strategy=LocatorStrategy.ACCESSIBILITY_ROLE, value="role:button;name:Add to cart", confidence=0.9)],
            ),
            ObservedElement(
                index=2, role="textbox", name="",
                locators=[Locator(strategy=LocatorStrategy.ACCESSIBILITY_ROLE, value="role:textbox", confidence=0.4)],
            ),
        ],
    )


def test_parses_role_and_name():
    role, name = parse_locator_value("role:button;name:Add to cart")
    assert role == "button"
    assert name == "Add to cart"


def test_parses_role_only_when_no_name_present():
    role, name = parse_locator_value("role:textbox")
    assert role == "textbox"
    assert name is None


def test_parses_name_with_special_characters():
    role, name = parse_locator_value("role:link;name:View Cart (3 items)")
    assert role == "link"
    assert name == "View Cart (3 items)"


def test_click_decision_becomes_click_step_with_correct_target():
    decision = AgentDecision(reasoning="add to cart", action="click", target_index=1, done=False)
    execution_step, artifact_step, param = decision_to_step(decision, _observation(), step_id=5)

    assert execution_step.step_id == 5
    assert execution_step.action == "click"
    assert execution_step.target is not None
    assert execution_step.target.locators[0].value == "role:button;name:Add to cart"
    assert artifact_step == execution_step
    assert param is None


def test_type_decision_carries_the_value_to_type():
    decision = AgentDecision(reasoning="enter username", action="type", target_index=2, value="standard_user", done=False)
    execution_step, artifact_step, param = decision_to_step(decision, _observation(), step_id=1)

    assert execution_step.action == "type"
    assert execution_step.value == "standard_user"
    assert artifact_step.value == "standard_user"
    assert param is None


def test_navigate_decision_has_no_target():
    decision = AgentDecision(reasoning="go to login", action="navigate", value="https://www.saucedemo.com/", done=False)
    execution_step, artifact_step, param = decision_to_step(decision, _observation(), step_id=1)

    assert execution_step.action == "navigate"
    assert execution_step.target is None
    assert execution_step.value == "https://www.saucedemo.com/"
    assert param is None


def test_invalid_target_index_raises_rather_than_silently_picking_something():
    decision = AgentDecision(reasoning="click something", action="click", target_index=99, done=False)
    with pytest.raises(ValueError):
        decision_to_step(decision, _observation(), step_id=1)


def test_unrecognized_action_raises_rather_than_being_silently_ignored():
    decision = AgentDecision(reasoning="do something weird", action="scroll", done=False)
    with pytest.raises(ValueError):
        decision_to_step(decision, _observation(), step_id=1)


def test_recorded_step_defaults_to_safe_risk_level():
    decision = AgentDecision(reasoning="click", action="click", target_index=1, done=False)
    execution_step, artifact_step, _ = decision_to_step(decision, _observation(), step_id=1)

    from capability_recorder.schema import RiskLevel
    assert execution_step.risk_level == RiskLevel.SAFE
    assert artifact_step.risk_level == RiskLevel.SAFE


def _observation_with_password_field() -> Observation:
    return Observation(
        url="https://www.saucedemo.com/",
        page_text="Login",
        elements=[
            ObservedElement(
                index=1, role="textbox", name="Password",
                locators=[Locator(strategy=LocatorStrategy.ACCESSIBILITY_ROLE, value="role:textbox;name:Password", confidence=0.9)],
            ),
        ],
    )


def test_is_sensitive_field_detects_common_keywords():
    from capability_recorder.agent import is_sensitive_field
    assert is_sensitive_field("Password") is True
    assert is_sensitive_field("password") is True
    assert is_sensitive_field("Social Security Number (SSN)") is True
    assert is_sensitive_field("PIN") is True
    assert is_sensitive_field("Username") is False
    assert is_sensitive_field("Search") is False


def test_execution_step_keeps_the_real_value_for_a_sensitive_field():
    decision = AgentDecision(reasoning="enter password", action="type", target_index=1, value="secret_sauce", done=False)
    execution_step, artifact_step, param = decision_to_step(decision, _observation_with_password_field(), step_id=1)

    assert execution_step.value == "secret_sauce"


def test_artifact_step_redacts_the_literal_value_for_a_sensitive_field():
    decision = AgentDecision(reasoning="enter password", action="type", target_index=1, value="secret_sauce", done=False)
    execution_step, artifact_step, param = decision_to_step(decision, _observation_with_password_field(), step_id=1)

    assert artifact_step.value != "secret_sauce"
    assert "secret_sauce" not in (artifact_step.value or "")
    assert artifact_step.value == "{{password}}"


def test_typing_into_a_sensitive_field_produces_a_matching_input_param():
    decision = AgentDecision(reasoning="enter password", action="type", target_index=1, value="secret_sauce", done=False)
    execution_step, artifact_step, param = decision_to_step(decision, _observation_with_password_field(), step_id=1)

    assert param is not None
    assert param.name == "password"
    assert param.required is True
    assert "secret_sauce" not in param.description


def test_typing_into_a_non_sensitive_field_is_not_redacted_in_either_step():
    decision = AgentDecision(reasoning="enter username", action="type", target_index=2, value="standard_user", done=False)
    execution_step, artifact_step, param = decision_to_step(decision, _observation(), step_id=1)

    assert execution_step.value == "standard_user"
    assert artifact_step.value == "standard_user"
    assert param is None