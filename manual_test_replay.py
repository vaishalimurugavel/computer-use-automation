"""
Manual, one-off script: REPLAY the Capability artifact produced by the
discovery run (manual_test_discovery.py), using PlaywrightStepExecutor --
no LLM call this time. This is the core "discover once, replay
deterministically forever" claim, demonstrated end-to-end.

Run with:
    python manual_test_replay.py

Expects evidence/example_capability.json to exist (save the JSON printed
by manual_test_discovery.py there first).
"""

import json

from playwright.sync_api import sync_playwright

from capability_recorder.executor import PlaywrightStepExecutor
from capability_recorder.replay import OutcomeCategory, replay_capability
from capability_recorder.schema import Capability


class AutoApproveEscalationHandler:
    """For this manual demo, auto-approve any escalation and print why it
    was asked -- a real system would pause for an actual human here."""

    def escalate(self, step, reason: str) -> bool:
        print(f"  [ESCALATION] step {step.step_id}: {reason} -- auto-approving for this demo")
        return True


def main():
    with open("evidence/example_capability.json") as f:
        capability = Capability.model_validate(json.load(f))

    print(f"Replaying capability: {capability.name} ({len(capability.steps)} steps)\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto("https://www.saucedemo.com/")

        executor = PlaywrightStepExecutor(page)
        escalation = AutoApproveEscalationHandler()

        result = replay_capability(capability, executor, escalation)

        print("\n=== REPLAY RESULT ===")
        print(f"Final category: {result.final_category}")
        print(f"Detail: {result.detail}")
        print("\nPer-step outcomes:")
        for outcome in result.step_outcomes:
            print(f"  step {outcome.step_id}: {outcome.category} -- {outcome.detail}")

        if result.final_category == OutcomeCategory.SUCCESS:
            print("\n✅ Replay succeeded -- no LLM was called.")
        else:
            print(f"\n⚠️ Replay did not fully succeed: {result.final_category}")

        input("\nPress Enter to close the browser...")
        browser.close()


if __name__ == "__main__":
    main()