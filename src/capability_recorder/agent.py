"""
Discovery agent: the LLM-driven loop that explores a UI to accomplish a
goal, recording each successful action as a Step, and assembling the
final sequence into a Capability artifact once the goal is reported done.

Design choice, consistent with perception.py and llm_client.py: the
PURE logic (turning an AgentDecision + Observation into a Step, parsing a
locator value back into role/name) is separated from the live,
Playwright-and-network-dependent orchestration loop. The pure pieces are
unit tested; discover_capability() itself is exercised via real runs
against SauceDemo/Demoblaze, not pytest, since it requires a live browser
and a real LLM call.
"""

from __future__ import annotations

from capability_recorder.llm_client import AgentDecision, get_next_action
from capability_recorder.perception import Observation, capture_observation
from capability_recorder.schema import (
    Capability,
    ElementTarget,
    InputParam,
    Locator,
    LocatorStrategy,
    OutputField,
    RiskLevel,
    Step,
    SuccessCondition,
)


class GoalNotReachedError(Exception):
    """Raised when discovery hits the max iteration cap without the LLM
    reporting done=True. Consistent with the 'never silently guess'
    principle -- an incomplete discovery run should be a visible failure,
    not a partial artifact presented as if it succeeded."""


def parse_locator_value(value: str) -> tuple[str, str | None]:
    """Parse a locator value like 'role:button;name:Add to cart' (or, if
    there's no accessible name, just 'role:button') back into (role, name).

    This is the inverse of perception.py's _build_locators() value format,
    and is what lets agent.py turn a stored Locator back into the pieces
    needed for a real Playwright get_by_role() call.
    """
    parts = value.split(";")
    role_part = parts[0]
    role = role_part.split(":", 1)[1] if ":" in role_part else role_part

    name = None
    if len(parts) > 1 and parts[1].startswith("name:"):
        name = parts[1].split(":", 1)[1]

    return role, name


def decision_to_step(decision: AgentDecision, observation: Observation, step_id: int) -> Step:
    """Turn one AgentDecision (the LLM's chosen action) into a recorded
    Step, using the ObservedElement it pointed to (if any) for the
    ElementTarget/Locators. Pure function -- no Playwright dependency.
    """
    action_map = {
        "click": "click",
        "type": "type",
        "navigate": "navigate",
        "extract": "extract",
    }
    if decision.action not in action_map:
        raise ValueError(f"Unrecognized action from LLM decision: {decision.action!r}")

    target: ElementTarget | None = None
    if decision.target_index is not None:
        matching = [el for el in observation.elements if el.index == decision.target_index]
        if not matching:
            raise ValueError(
                f"LLM chose target_index={decision.target_index}, which does not exist "
                f"in the current observation (has {len(observation.elements)} elements)."
            )
        element = matching[0]
        target = ElementTarget(
            description=f"{element.role} \"{element.name}\"" if element.name else element.role,
            locators=element.locators,
        )

    return Step(
        step_id=step_id,
        action=action_map[decision.action],
        target=target,
        value=decision.value,
        risk_level=RiskLevel.SAFE,  # static risk classification is a documented next step -- see REPORT.md
    )


def _execute_on_page(page, step: Step) -> None:
    """Thin Playwright-dependent execution of one recorded step. Not unit
    tested -- requires a live page. Kept minimal and single-purpose so the
    mapping from Locator -> real Playwright call is easy to audit.

    KNOWN LIMITATION (confirmed via a live run against SauceDemo): role +
    accessible name is not always sufficient to uniquely identify an
    element. SauceDemo has six products, each with an "Add to cart" button
    sharing the identical accessible name -- get_by_role(name="Add to
    cart") legitimately matches all six, and Playwright's strict mode
    refuses to click an ambiguous locator rather than guessing.

    MVP fix: fall back to .first when a locator resolves to multiple
    elements, rather than failing outright. This unblocks execution but is
    NOT a correct disambiguation -- it will click whichever matching
    element happens to be first in the DOM, which may not be the one the
    LLM actually intended. The correct fix -- extending perception.py to
    surface a unique, disambiguating locator (e.g. a data-test attribute,
    or nearby product-name context) when multiple elements share an
    accessible name -- is a documented next step, not implemented here.
    See REPORT.md "Cuts".
    """
    if step.action == "navigate":
        page.goto(step.value)
        return

    if step.target is None:
        raise ValueError(f"Step {step.step_id} has action={step.action!r} but no target element.")

    locator_value = step.target.locators[0].value
    role, name = parse_locator_value(locator_value)
    element = page.get_by_role(role, name=name) if name else page.get_by_role(role)

    if element.count() > 1:
        element = element.first  # MVP fallback -- see docstring above

    if step.action == "click":
        element.click()
    elif step.action == "type":
        element.fill(step.value or "")
    elif step.action == "extract":
        element.text_content()  # discovery-time only; not yet wired into an output field -- see REPORT.md

def discover_capability(
    page,
    goal: str,
    api_key: str,
    capability_id: str,
    name: str,
    target_app: str,
    success_check_text: str,
    max_iterations: int = 15,
) -> Capability:
    """Run the discovery loop against a live Playwright page: observe,
    ask the LLM for the next action, execute it, record it as a Step,
    repeat until the LLM reports done or max_iterations is hit.

    `success_check_text` is a simple substring expected to appear on the
    page once the goal is genuinely accomplished (e.g. "Checkout: Complete!")
    -- used to build the resulting Capability's SuccessCondition. Auto-
    inferring this from the LLM's own reasoning is a documented next step
    rather than something this MVP attempts -- see REPORT.md "Cuts".
    """
    steps: list[Step] = []
    history: list[AgentDecision] = []

    for iteration in range(1, max_iterations + 1):
        observation = capture_observation(page)
        decision = get_next_action(goal, observation, history, api_key)
        history.append(decision)

        if decision.done:
            break

        step = decision_to_step(decision, observation, step_id=iteration)
        _execute_on_page(page, step)
        steps.append(step)
    else:
        raise GoalNotReachedError(
            f"Goal not reached after {max_iterations} iterations: {goal!r}"
        )

    return Capability(
        capability_id=capability_id,
        name=name,
        version=1,
        target_app=target_app,
        description=goal,
        input_params=[],
        output_fields=[],
        steps=steps,
        success_condition=SuccessCondition(
            description=f"Page contains: {success_check_text!r}",
            check=ElementTarget(
                description="Success indicator",
                locators=[
                    Locator(
                        strategy=LocatorStrategy.TEXT_CONTENT,
                        value=success_check_text,
                        confidence=0.7,
                        reasoning="Auto-generated from success_check_text; not independently verified against the live page at recording time -- see REPORT.md Cuts.",
                    )
                ],
            ),
        ),
    )