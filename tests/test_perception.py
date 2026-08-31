"""
Tests for perception.py's flattening logic.

Deliberately uses plain dict fixtures shaped like Playwright's
page.accessibility.snapshot() output, rather than a real Page -- this
tests the indexing/filtering logic in isolation, with no browser
dependency at all.
"""

from capability_recorder.perception import flatten_accessibility_tree
from capability_recorder.schema import LocatorStrategy


def test_flattens_nested_tree_into_indexed_list():
    snapshot = {
        "role": "WebArea",
        "name": "SauceDemo",
        "children": [
            {"role": "generic", "name": "", "children": [
                {"role": "button", "name": "Add to Cart"},
            ]},
            {"role": "link", "name": "View Cart"},
        ],
    }

    elements = flatten_accessibility_tree(snapshot)

    assert len(elements) == 2
    assert elements[0].index == 1
    assert elements[0].role == "button"
    assert elements[0].name == "Add to Cart"
    assert elements[1].index == 2
    assert elements[1].role == "link"
    assert elements[1].name == "View Cart"


def test_non_interactive_roles_are_excluded():
    snapshot = {
        "role": "WebArea",
        "name": "",
        "children": [
            {"role": "heading", "name": "Products"},
            {"role": "generic", "name": "container"},
            {"role": "button", "name": "Checkout"},
        ],
    }

    elements = flatten_accessibility_tree(snapshot)

    assert len(elements) == 1
    assert elements[0].role == "button"
    assert elements[0].name == "Checkout"


def test_indices_are_sequential_and_start_at_one():
    snapshot = {
        "role": "WebArea",
        "name": "",
        "children": [
            {"role": "textbox", "name": "Username"},
            {"role": "textbox", "name": "Password"},
            {"role": "button", "name": "Login"},
        ],
    }

    elements = flatten_accessibility_tree(snapshot)

    assert [e.index for e in elements] == [1, 2, 3]


def test_element_with_name_gets_high_confidence_role_and_name_locator():
    snapshot = {"role": "button", "name": "Add to Cart", "children": []}

    elements = flatten_accessibility_tree(snapshot)

    assert len(elements) == 1
    locator = elements[0].locators[0]
    assert locator.strategy == LocatorStrategy.ACCESSIBILITY_ROLE
    assert locator.value == "role:button;name:Add to Cart"
    assert locator.confidence == 0.9


def test_element_without_name_gets_lower_confidence_role_only_locator():
    snapshot = {"role": "button", "name": "", "children": []}

    elements = flatten_accessibility_tree(snapshot)

    assert len(elements) == 1
    locator = elements[0].locators[0]
    assert locator.value == "role:button"
    assert locator.confidence == 0.4
    assert locator.reasoning is not None


def test_empty_snapshot_produces_no_elements():
    assert flatten_accessibility_tree({}) == []


def test_deeply_nested_children_are_still_found():
    snapshot = {
        "role": "WebArea", "name": "", "children": [
            {"role": "generic", "name": "", "children": [
                {"role": "generic", "name": "", "children": [
                    {"role": "generic", "name": "", "children": [
                        {"role": "button", "name": "Deeply Nested Button"},
                    ]},
                ]},
            ]},
        ],
    }

    elements = flatten_accessibility_tree(snapshot)

    assert len(elements) == 1
    assert elements[0].name == "Deeply Nested Button"