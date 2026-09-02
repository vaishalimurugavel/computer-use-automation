"""
LLM client: asks the model "what should I do next?" given the goal, the
current observation, and action history so far -- and parses its response
into a structured AgentDecision.

Design choice: plain-prompt JSON instruction (ask the model to respond
ONLY in JSON) rather than a provider-specific structured-output/schema
mode. Simpler and more transparent to reason about, at the cost of needing
defensive parsing on the response (see parse_llm_response) since the model
could in principle return malformed or fenced JSON.

As with perception.py: build_prompt() and parse_llm_response() are PURE
functions with no network dependency, and are fully unit tested. Only
call_gemini() actually makes a network call and requires a real API key --
it is exercised via real runs, not unit tests.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

from capability_recorder.perception import Observation


class AgentDecision(BaseModel):
    reasoning: str
    action: str          # "click" | "type" | "navigate" | "extract" | "done"
    target_index: int | None = None   # index into Observation.elements; None for e.g. "done"
    value: str | None = None          # e.g. text to type
    done: bool = False


class LLMResponseParseError(Exception):
    """Raised when the model's response can't be parsed into a valid
    AgentDecision. Callers should treat this the same way replay.py
    treats an unrecognized state: escalate, never guess."""


def build_prompt(goal: str, observation: Observation, history: list[AgentDecision]) -> str:
    element_lines = "\n".join(
        f"[{el.index}] {el.role} \"{el.name}\"" if el.name else f"[{el.index}] {el.role} (no accessible name)"
        for el in observation.elements
    )

    history_lines = "\n".join(
        f"- {d.action} on [{d.target_index}]" + (f' with value "{d.value}"' if d.value else "")
        for d in history
    ) or "(no actions taken yet)"

    return f"""You are controlling a web browser to accomplish a goal.

GOAL: {goal}

CURRENT PAGE URL: {observation.url}

INTERACTIVE ELEMENTS ON THIS PAGE:
{element_lines}

ACTIONS TAKEN SO FAR:
{history_lines}

Decide the SINGLE next action to take. Respond with ONLY a JSON object,
no other text, no markdown code fences, in exactly this shape:

{{"reasoning": "<why you are taking this action>", "action": "<click|type|navigate|extract|done>", "target_index": <element index or null>, "value": "<text to type, or null>", "done": <true|false>}}

If the goal has been fully accomplished, set "action" to "done" and "done" to true.
"""


def parse_llm_response(raw_text: str) -> AgentDecision:
    """Defensively parse the model's response into an AgentDecision.

    Handles the common failure mode of a model wrapping its JSON in
    markdown code fences (```json ... ```) despite being told not to.
    Raises LLMResponseParseError (never returns a guessed/default
    decision) if the response can't be parsed or doesn't match the
    expected shape -- consistent with the "never silently guess" default
    established in replay.py.
    """
    cleaned = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise LLMResponseParseError(f"Model response was not valid JSON: {raw_text!r}") from e

    try:
        return AgentDecision.model_validate(data)
    except ValidationError as e:
        raise LLMResponseParseError(f"Model response JSON did not match expected shape: {data!r}") from e


def call_gemini(prompt: str, api_key: str, model: str = "gemini-3.6-flash") -> str:
    """Thin wrapper around the actual Gemini API call. Not unit tested --
    requires network access and a real API key. Kept as a single-purpose
    function (prompt in, raw text out) so it's trivially swappable for a
    different provider without touching build_prompt/parse_llm_response.

    NOTE ON SDK HISTORY: originally written against the `google-generativeai`
    package, which Google has since fully deprecated in favor of the unified
    `google-genai` SDK (confirmed via a live run: the old package emitted a
    FutureWarning stating all support had ended, and the old model name
    'gemini-1.5-flash' returned a 404 NotFound against the current API).
    Migrated to `google-genai` accordingly.
    """
    from google import genai  # local import: only required if this function is actually called

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(model=model, contents=prompt)
    return response.text


def get_next_action(
    goal: str,
    observation: Observation,
    history: list[AgentDecision],
    api_key: str,
) -> AgentDecision:
    """Orchestrates one full decision turn: build prompt -> call model ->
    parse response. This is the function agent.py's loop will call.
    """
    prompt = build_prompt(goal, observation, history)
    raw_response = call_gemini(prompt, api_key)
    return parse_llm_response(raw_response)