"""
Tests for perception.py's flattening logic.

Deliberately uses plain YAML-text fixtures shaped like Playwright's
page.locator(...).aria_snapshot() output, rather than a real Page -- this
tests the indexing/filtering logic in isolation, with no browser
dependency at all.

NOTE: this replaces an earlier version of these tests written against the
removed page.accessibility.snapshot() dict format. Confirmed against a
real SauceDemo run with Playwright 1.62.0 that aria_snapshot() is the
current API and returns YAML-style text, not a nested dict.
"""

from capability_recorder.perception import flatten_accessibility_tree
from capability_recorder.schema import LocatorStrategy


def test_flattens_real_saucedemo_login_page_snapshot():
    """This exact text is what aria_snapshot() returned for a live run
    against https://www.saucedemo.com/ -- kept as a regression fixture."""
    snapshot_text = """- text: Swag Labs
- textbox "Username"
- textbox "Password"
- button "Login"
- heading "Accepted usernames are:" [level=4]
- text: standard_user locked_out_user problem_user performance_glitch_user error_user visual_user
- heading "Password for all users:" [level=4]
- text: secret_sauce"""

    elements = flatten_accessibility_tree(snapshot_text)

    assert len(elements) == 3
    assert elements[0].role == "textbox"
    assert elements[0].name == "Username"
    assert elements[1].role == "textbox"
    assert elements[1].name == "Password"
    assert elements[2].role == "button"
    assert elements[2].name == "Login"


def test_non_interactive_roles_are_excluded():
    snapshot_text = """- heading "Products" [level=2]
- text: some generic text
- button "Checkout"
- text: standard_user"""

    elements = flatten_accessibility_tree(snapshot_text)

    assert len(elements) == 1
    assert elements[0].role == "button"
    assert elements[0].name == "Checkout"


def test_indices_are_sequential_and_start_at_one():
    snapshot_text = """- textbox "Username"
- textbox "Password"
- button "Login\""""

    elements = flatten_accessibility_tree(snapshot_text)

    assert [e.index for e in elements] == [1, 2, 3]


def test_element_with_name_gets_high_confidence_role_and_name_locator():
    snapshot_text = '- button "Add to cart"'

    elements = flatten_accessibility_tree(snapshot_text)

    assert len(elements) == 1
    locator = elements[0].locators[0]
    assert locator.strategy == LocatorStrategy.ACCESSIBILITY_ROLE
    assert locator.value == "role:button;name:Add to cart"
    assert locator.confidence == 0.9


def test_element_without_name_gets_lower_confidence_role_only_locator():
    snapshot_text = "- button"

    elements = flatten_accessibility_tree(snapshot_text)

    assert len(elements) == 1
    locator = elements[0].locators[0]
    assert locator.value == "role:button"
    assert locator.confidence == 0.4
    assert locator.reasoning is not None


def test_empty_snapshot_produces_no_elements():
    assert flatten_accessibility_tree("") == []


def test_lines_with_extra_attributes_still_parse_correctly():
    """aria_snapshot() sometimes appends attributes like [level=4] --
    these should not break role/name extraction."""
    snapshot_text = '- heading "Section Title" [level=4]\n- button "Submit"'

    elements = flatten_accessibility_tree(snapshot_text)

    assert len(elements) == 1  # heading is not in INTERACTIVE_ROLES
    assert elements[0].role == "button"
    assert elements[0].name == "Submit"


def test_indented_lines_are_still_parsed_regardless_of_nesting_depth():
    """Indentation conveys tree nesting in aria_snapshot() output, but we
    intentionally flatten regardless of depth."""
    snapshot_text = """- generic:
  - button "Nested Button"
  - generic:
    - textbox "Deeply Nested Field\""""

    elements = flatten_accessibility_tree(snapshot_text)

    assert len(elements) == 2
    assert elements[0].name == "Nested Button"
    assert elements[1].name == "Deeply Nested Field"