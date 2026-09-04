"""
Tests for safety.py's allowlist enforcement logic.

Pure function tests -- no Playwright, no browser dependency. Covers
action-type checking (applies to every step) and domain checking
(applies to NAVIGATE steps, whose target URL is known directly from
step.value).
"""

from capability_recorder.safety import Allowlist, check_allowlist
from capability_recorder.schema import ActionType, Step


def test_allowed_action_and_domain_passes():
    allowlist = Allowlist(allowed_domains=["saucedemo.com"])
    step = Step(step_id=1, action=ActionType.NAVIGATE, value="https://www.saucedemo.com/cart.html")

    assert check_allowlist(step, allowlist) is None


def test_disallowed_action_type_is_rejected():
    allowlist = Allowlist(allowed_domains=["saucedemo.com"], allowed_actions=[ActionType.CLICK])
    step = Step(step_id=1, action=ActionType.TYPE, value="hello")

    violation = check_allowlist(step, allowlist)

    assert violation is not None
    assert "type" in violation.lower()


def test_navigate_to_disallowed_domain_is_rejected():
    allowlist = Allowlist(allowed_domains=["saucedemo.com"])
    step = Step(step_id=1, action=ActionType.NAVIGATE, value="https://evil-example.com/phish")

    violation = check_allowlist(step, allowlist)

    assert violation is not None
    assert "evil-example.com" in violation


def test_navigate_to_subdomain_of_allowed_domain_passes():
    allowlist = Allowlist(allowed_domains=["saucedemo.com"])
    step = Step(step_id=1, action=ActionType.NAVIGATE, value="https://checkout.saucedemo.com/pay")

    assert check_allowlist(step, allowlist) is None


def test_click_and_type_steps_are_not_domain_checked_in_this_mvp():
    allowlist = Allowlist(allowed_domains=["saucedemo.com"])
    step = Step(step_id=1, action=ActionType.CLICK)

    assert check_allowlist(step, allowlist) is None


def test_default_allowed_actions_permit_all_standard_action_types():
    allowlist = Allowlist(allowed_domains=["example.com"])
    for action in (ActionType.CLICK, ActionType.TYPE, ActionType.NAVIGATE, ActionType.EXTRACT, ActionType.WAIT_FOR):
        step = Step(step_id=1, action=action, value="https://www.example.com/")
        assert check_allowlist(step, allowlist) is None