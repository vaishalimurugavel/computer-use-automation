"""
Perception: turns "what's currently on screen" into a structured,
LLM-consumable Observation.

Design choice: the core logic (flatten_accessibility_tree) is a PURE
function operating on plain text -- the YAML-style string Playwright's
page.locator(...).aria_snapshot() returns -- rather than depending on a
live Playwright Page object directly. This means the indexing/flattening
logic can be tested with fast, fake fixtures with no browser dependency
at all. Only capture_observation() (a thin wrapper) actually touches
Playwright, and it is exercised via real runs against SauceDemo/Demoblaze
rather than unit tests, since it requires a live page.

NOTE ON API HISTORY: this module was originally written against
page.accessibility.snapshot(), which returned a nested dict. That API has
since been removed from Playwright; aria_snapshot() (confirmed present in
1.62.0, the version used in this project) is the current replacement, and
returns YAML-style text instead. This was caught via a live manual test
against SauceDemo, not by the automated test suite, since the original
tests used fake dicts that encoded the old (now-incorrect) assumption
about the API's shape -- a good example of why live verification against
a real target matters even with a passing unit test suite.

Text-only for now (no screenshots) -- see README/REPORT for why this is a
reasonable v1 choice given SauceDemo/Demoblaze have decent accessibility
metadata; screenshot-based perception is a documented extension point for
messier/legacy targets.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from capability_recorder.schema import Locator, LocatorStrategy

# Roles worth surfacing to the agent as candidate actions. Deliberately a
# fixed, curated set rather than "every line in the snapshot" -- most
# accessibility trees contain many non-interactive structural nodes
# (headings, generic containers, plain text) that would only add noise
# for the LLM.
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


_LINE_PATTERN = re.compile(
    r'^\s*-\s*([a-zA-Z]+)(?:\s+"([^"]*)")?'
)


def flatten_accessibility_tree(snapshot_text: str) -> list[ObservedElement]:
    """Parse the YAML-style text Playwright's page.locator(...).aria_snapshot()
    returns, producing a flat, indexed list of interactive elements only.

    Each line of aria_snapshot() output looks like:
        - button "Login"
        - textbox "Username"
        - heading "Accepted usernames are:" [level=4]
    Indentation conveys nesting, but for our purposes we only need a flat
    list of interactive elements in document order, so indentation is
    intentionally ignored.
    """
    elements: list[ObservedElement] = []
    for line in snapshot_text.splitlines():
        match = _LINE_PATTERN.match(line)
        if not match:
            continue
        role, name = match.group(1), match.group(2) or ""
        if role not in INTERACTIVE_ROLES:
            continue
        index = len(elements) + 1
        elements.append(
            ObservedElement(
                index=index,
                role=role,
                name=name,
                locators=_build_locators(role, name),
            )
        )
    return elements


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
    snapshot_text = page.locator("body").aria_snapshot()
    elements = flatten_accessibility_tree(snapshot_text)
    return Observation(
        url=page.url,
        page_text=page.inner_text("body"),
        elements=elements,
    )