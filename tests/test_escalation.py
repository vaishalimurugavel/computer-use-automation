"""
Tests for escalation.py.

ConsoleEscalationHandler is tested by injecting a fake input function
(rather than mocking the built-in input() directly) -- this exercises
the real approval logic without needing actual console interaction.
"""

from capability_recorder.escalation import ConsoleEscalationHandler, NullEscalationHandler
from capability_recorder.schema import ActionType, ElementTarget, Locator, LocatorStrategy, Step


def _step() -> Step:
    return Step(
        step_id=3,
        action=ActionType.CLICK,
        target=ElementTarget(
            description="Confirm and Create button",
            locators=[Locator(strategy=LocatorStrategy.ACCESSIBILITY_ROLE, value="role:button;name:Confirm and Create", confidence=0.9)],
        ),
    )


def test_console_handler_approves_on_y(capsys):
    handler = ConsoleEscalationHandler(input_func=lambda prompt: "y")
    result = handler.escalate(_step(), reason="risky action requires confirmation")
    assert result is True


def test_console_handler_approves_on_yes_case_insensitive(capsys):
    handler = ConsoleEscalationHandler(input_func=lambda prompt: "YES")
    result = handler.escalate(_step(), reason="risky action requires confirmation")
    assert result is True


def test_console_handler_denies_on_n(capsys):
    handler = ConsoleEscalationHandler(input_func=lambda prompt: "n")
    result = handler.escalate(_step(), reason="risky action requires confirmation")
    assert result is False


def test_console_handler_denies_on_empty_input():
    handler = ConsoleEscalationHandler(input_func=lambda prompt: "")
    result = handler.escalate(_step(), reason="risky action requires confirmation")
    assert result is False


def test_console_handler_denies_on_garbage_input():
    handler = ConsoleEscalationHandler(input_func=lambda prompt: "maybe idk")
    result = handler.escalate(_step(), reason="risky action requires confirmation")
    assert result is False


def test_console_handler_prints_step_details(capsys):
    handler = ConsoleEscalationHandler(input_func=lambda prompt: "n")
    handler.escalate(_step(), reason="risky action requires confirmation")

    captured = capsys.readouterr()
    assert "Step 3" in captured.out
    assert "Confirm and Create button" in captured.out
    assert "risky action requires confirmation" in captured.out


def test_null_handler_always_denies_without_prompting():
    handler = NullEscalationHandler()
    result = handler.escalate(_step(), reason="anything at all")
    assert result is False