"""
Real Playwright implementation of the StepExecutor protocol (see
replay.py). This is the "other side" of the seam: replay.py's control
logic (risk gating, outcome classification, recovery) was built and
tested entirely against FakeStepExecutor; this module is what lets the
exact same replay_capability() function run against a real browser,
with zero changes to replay.py itself.

Not unit tested -- requires a live Playwright page. Exercised via a real
replay run against the Capability artifact produced by agent.py's
discovery run (see manual_test_replay.py).
"""

from __future__ import annotations

from capability_recorder.agent import parse_locator_value
from capability_recorder.replay import ExecutionResult
from capability_recorder.schema import Step


class PlaywrightStepExecutor:
    """Implements StepExecutor (execute, observe_current_state) against a
    real Playwright page. Unlike agent.py's _execute_on_page (which raises
    on failure, since discovery-time errors should surface immediately),
    this NEVER raises -- every failure is caught and turned into a failed
    ExecutionResult, since replay.py's whole design depends on being able
    to classify a failure (business outcome / recoverable / hard failure)
    rather than crash on it.
    """

    def __init__(self, page):
        self.page = page

    def execute(self, step: Step) -> ExecutionResult:
        try:
            if step.action == "navigate":
                self.page.goto(step.value)
                return ExecutionResult(succeeded=True, observed_state=self._observe())

            if step.target is None:
                return ExecutionResult(
                    succeeded=False,
                    observed_state=self._observe(),
                    error_message=f"Step {step.step_id} (action={step.action}) has no target element.",
                )

            locator_value = step.target.locators[0].value
            role, name = parse_locator_value(locator_value)
            element = self.page.get_by_role(role, name=name) if name else self.page.get_by_role(role)

            # Same MVP fallback as agent.py's discovery-time execution --
            # see agent.py's _execute_on_page docstring for the known
            # limitation this papers over (role+name isn't always unique).
            if element.count() > 1:
                element = element.first

            if step.action == "click":
                element.click()
            elif step.action == "type":
                element.fill(step.value or "")
            elif step.action == "extract":
                element.text_content()
            else:
                return ExecutionResult(
                    succeeded=False,
                    observed_state=self._observe(),
                    error_message=f"Unrecognized action: {step.action!r}",
                )

            return ExecutionResult(succeeded=True, observed_state=self._observe())

        except Exception as e:
            return ExecutionResult(
                succeeded=False,
                observed_state=self._observe_safe(),
                error_message=str(e),
            )

    def observe_current_state(self) -> str:
        return self._observe()

    def _observe(self) -> str:
        return self.page.inner_text("body")

    def _observe_safe(self) -> str:
        """Best-effort observation for use inside an except block -- if
        the page itself is in a bad state (e.g. mid-navigation crash),
        even reading body text could fail. Never let observation itself
        raise and mask the original error."""
        try:
            return self.page.inner_text("body")
        except Exception:
            return ""