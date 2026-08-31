"""
Perception: turns "what's currently on screen" into a structured,
LLM-consumable Observation.

Design choice: the core logic (flatten_accessibility_tree) is a PURE
function operating on a plain dict -- the same shape Playwright's
page.accessibility.snapshot() returns -- rather than depending on a live
Playwright Page object directly. This means the indexing/flattening logic
can be tested with fast, fake fixtures with no browser dependency at all.
Only capture_observation() (a thin wrapper) actually touches Playwright,
and it is exercised via real runs against SauceDemo/Demoblaze rather than
unit tests, since it requires a live page.

Text-only for now (no screenshots) -- see README/REPORT for why this is a
reasonable v1 choice given SauceDemo/Demoblaze have decent accessibility
metadata; screenshot-based perception is a documented extension point for
messier/legacy targets.
"""

from __future__ import annotations

from pydantic import BaseModel

from capability_recorder.schema import Locator, LocatorStrategy

# Roles worth surfacing to the agent as candidate actions. Deliberately a
# fixed, curated set rather than "every node in the tree" -- most
# accessibility trees contain many non-interactive structural nodes
# (headings, generic containers) that would only add noise for the LLM.
INTERACTIVE_ROLES = {
    "button",
    "link",
    "textbox",
    "checkbox",
    "radio",
    "combobox",
    "menuitem",
    "tab",
    "switch",
}


class ObservedElement(BaseModel):
    index: int
    role: str
    name: str
    locators: list[Locator]


class Observation(BaseModel):
    url: str
    page_text: str
    elements: list[ObservedElement]


def flatten_accessibility_tree(snapshot: dict) -> list[ObservedElement]:
    """Walk a raw accessibility-tree snapshot dict and produce a flat,
    indexed list of interactive elements only.

    `snapshot` has the shape Playwright's page.accessibility.snapshot()
    returns: {"role": str, "name": str, "children": [...]} (recursively).
    Kept as a plain dict parameter (not a Playwright type) specifically so
    this function has zero Playwright dependency and can be unit tested
    with fake fixtures.
    """
    elements: list[ObservedElement] = []
    _walk(snapshot, elements)
    return elements


def _walk(node: dict, elements: list[ObservedElement]) -> None:
    if not node:
        return

    role = node.get("role", "")
    name = node.get("name", "") or ""

    if role in INTERACTIVE_ROLES:
        index = len(elements) + 1
        elements.append(
            ObservedElement(
                index=index,
                role=role,
                name=name,
                locators=_build_locators(role, name),
            )
        )

    for child in node.get("children", []) or []:
        _walk(child, elements)


def _build_locators(role: str, name: str) -> list[Locator]:
    """Build the locator list for one observed element. Accessibility
    role+name is the primary strategy (matches the hybrid approach decided
    during design: accessibility-first, since it degrades more gracefully
    on messy markup than raw CSS selectors). Confidence is lower when the
    element has no accessible name at all, since role-only matching is
    much less specific.
    """
    if name:
        return [
            Locator(
                strategy=LocatorStrategy.ACCESSIBILITY_ROLE,
                value=f"role:{role};name:{name}",
                confidence=0.9,
            )
        ]
    return [
        Locator(
            strategy=LocatorStrategy.ACCESSIBILITY_ROLE,
            value=f"role:{role}",
            confidence=0.4,
            reasoning="No accessible name present on this element; role-only match is much less specific.",
        )
    ]


def capture_observation(page) -> Observation:
    """Thin Playwright-dependent wrapper. Not unit tested directly -- see
    module docstring. `page` is a playwright.sync_api.Page, but the type
    is intentionally left unannotated here to avoid a hard import-time
    dependency on playwright for anything that only needs
    flatten_accessibility_tree.
    """
    snapshot = page.accessibility.snapshot() or {}
    elements = flatten_accessibility_tree(snapshot)
    return Observation(
        url=page.url,
        page_text=page.inner_text("body"),
        elements=elements,
    )