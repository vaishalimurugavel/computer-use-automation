"""
Tests for llm_client.py's pure logic: build_prompt and parse_llm_response.

call_gemini() and get_next_action() are NOT tested here -- they require a
real network call and API key. These tests cover everything that can go
wrong or needs verifying without ever touching the network.
"""

import pytest

from capability_recorder.llm_client import (
    AgentDecision,
    LLMResponseParseError,
    build_prompt,
    parse_llm_response,
)
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
                index=2, role="link", name="Cart",
                locators=[Locator(strategy=LocatorStrategy.ACCESSIBILITY_ROLE, value="role:link;name:Cart", confidence=0.9)],
            ),
        ],
    )


def test_prompt_includes_the_goal():
    prompt = build_prompt("Add an item to cart", _observation(), [])
    assert "Add an item to cart" in prompt


def test_prompt_lists_all_elements_with_their_indices():
    prompt = build_prompt("goal", _observation(), [])
    assert "[1] button \"Add to cart\"" in prompt
    assert "[2] link \"Cart\"" in prompt


def test_prompt_shows_no_actions_message_when_history_is_empty():
    prompt = build_prompt("goal", _observation(), [])
    assert "no actions taken yet" in prompt


def test_prompt_includes_prior_actions_from_history():
    history = [AgentDecision(reasoning="clicking add to cart", action="click", target_index=1, done=False)]
    prompt = build_prompt("goal", _observation(), history)
    assert "click on [1]" in prompt


def test_prompt_instructs_json_only_response():
    prompt = build_prompt("goal", _observation(), [])
    assert "ONLY a JSON object" in prompt


def test_parses_clean_json_response():
    raw = '{"reasoning": "clicking add to cart", "action": "click", "target_index": 1, "value": null, "done": false}'
    decision = parse_llm_response(raw)
    assert decision.action == "click"
    assert decision.target_index == 1
    assert decision.done is False


def test_parses_json_wrapped_in_markdown_code_fence():
    raw = '```json\n{"reasoning": "done", "action": "done", "target_index": null, "value": null, "done": true}\n```'
    decision = parse_llm_response(raw)
    assert decision.action == "done"
    assert decision.done is True


def test_parses_json_wrapped_in_plain_code_fence_without_language_tag():
    raw = '```\n{"reasoning": "typing username", "action": "type", "target_index": 3, "value": "standard_user", "done": false}\n```'
    decision = parse_llm_response(raw)
    assert decision.action == "type"
    assert decision.value == "standard_user"


def test_raises_parse_error_on_invalid_json():
    with pytest.raises(LLMResponseParseError):
        parse_llm_response("this is not json at all")


def test_raises_parse_error_when_required_fields_are_missing():
    with pytest.raises(LLMResponseParseError):
        parse_llm_response('{"target_index": 1, "done": false}')


def test_raises_parse_error_rather_than_returning_a_default_decision():
    with pytest.raises(LLMResponseParseError):
        parse_llm_response("{}")