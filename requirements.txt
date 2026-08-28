"""
Core artifact schema for the Capability Recorder system.

An "artifact" (Capability) represents one recorded, human-approved,
end-to-end flow discovered by an LLM agent, which can later be replayed
deterministically without invoking the LLM again.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Element targeting
# ---------------------------------------------------------------------------

class LocatorStrategy(str, Enum):
    """Ordered by general robustness, most robust first.
    The replay engine tries strategies for a given element in the order
    they appear in ElementTarget.locators, not necessarily in this enum's
    declaration order -- that order is decided per-element at recording time.
    """
    ACCESSIBILITY_ROLE = "accessibility_role"   # role + accessible name
    CSS_SELECTOR = "css_selector"
    TEXT_CONTENT = "text_content"
    XPATH = "xpath"                              # last resort, most brittle


class Locator(BaseModel):
    strategy: LocatorStrategy
    value: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: Optional[str] = None
    # e.g. "no accessible name present on this element; falling back to CSS selector"


class ElementTarget(BaseModel):
    # Ordered by preference: replay engine tries these in order until one resolves.
    locators: list[Locator]
    description: str  # human-readable, e.g. "Add to Cart button on product page"


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

class ActionType(str, Enum):
    CLICK = "click"
    TYPE = "type"
    NAVIGATE = "navigate"
    WAIT_FOR = "wait_for"
    EXTRACT = "extract"       # read data from the page, not just act


class RiskLevel(str, Enum):
    SAFE = "safe"     # reversible, low-stakes - proceed automatically
    RISKY = "risky"   # irreversible or high-stakes - requires human confirmation


class RecoverableConditionType(str, Enum):
    KNOWN_DIALOG_DISMISS = "known_dialog_dismiss"
    TRANSIENT_RETRY = "transient_retry"
    SESSION_TIMEOUT_REAUTH = "session_timeout_reauth"


class RecoveryRule(BaseModel):
    """A rule the replay engine checks for at every step, regardless of
    whether that step "succeeded" or "failed" in the narrow sense --
    e.g. a session-timeout dialog can interrupt any capability at any point.
    """
    trigger: str  # e.g. "text_contains:session expired" -- matcher spec, engine-interpreted
    condition_type: RecoverableConditionType
    recovery_action: str  # e.g. "dismiss_dialog", "reauth_and_retry"


class Step(BaseModel):
    step_id: int
    action: ActionType
    target: Optional[ElementTarget] = None      # None for e.g. NAVIGATE
    value: Optional[str] = None                  # e.g. text to type; may reference an input param by name
    extract_as: Optional[str] = None             # if action == EXTRACT, name of the output field written
    risk_level: RiskLevel = RiskLevel.SAFE
    dynamic_risk_check: Optional[str] = None
    # e.g. "amount > 1000" -- evaluated against actual input params at replay time;
    # can upgrade a statically SAFE step to requiring confirmation for this specific invocation.


# ---------------------------------------------------------------------------
# Business outcomes (pre-registered, so replay never needs to guess or re-invoke an LLM)
# ---------------------------------------------------------------------------

class BusinessOutcomeType(str, Enum):
    NOT_FOUND = "not_found"
    VALIDATION_ERROR = "validation_error"
    PERMISSION_DENIED = "permission_denied"
    # extensible -- new named outcomes get added as they're discovered


class ExpectedBusinessOutcome(BaseModel):
    after_step_id: int
    trigger: str  # e.g. "text_contains:No member found"
    outcome_type: BusinessOutcomeType
    description: str


# ---------------------------------------------------------------------------
# Input / output contract
# ---------------------------------------------------------------------------

class InputParam(BaseModel):
    name: str
    type: str  # kept simple ("string", "int", ...) rather than full JSON schema for v1
    required: bool
    description: str


class OutputField(BaseModel):
    name: str
    type: str
    description: str


class SuccessCondition(BaseModel):
    description: str
    check: ElementTarget  # e.g. "confirmation banner visible" -- element that must resolve


# ---------------------------------------------------------------------------
# Versioning / provenance (supports the human-resolution feedback loop)
# ---------------------------------------------------------------------------

class RevisionReason(str, Enum):
    INITIAL_RECORDING = "initial_recording"
    HUMAN_RESOLUTION = "human_resolution"
    # a human resolved a previously-unrecognized replay state and the artifact
    # was updated (e.g. a new ExpectedBusinessOutcome or RecoveryRule added)


class RevisionMetadata(BaseModel):
    version: int
    reason: RevisionReason
    changed_by: str
    changed_at: datetime
    note: str  # human-readable summary of what changed and why


# ---------------------------------------------------------------------------
# The artifact itself
# ---------------------------------------------------------------------------

class Capability(BaseModel):
    capability_id: str
    name: str  # human-readable, e.g. "add_item_to_cart_and_checkout"
    version: int
    target_app: str  # e.g. "saucedemo" -- relevant for future multi-tenant reuse
    description: str

    input_params: list[InputParam] = []
    output_fields: list[OutputField] = []
    steps: list[Step]
    success_condition: SuccessCondition

    expected_business_outcomes: list[ExpectedBusinessOutcome] = []
    local_recovery_rules: list[RecoveryRule] = []
    # Global recovery rules (session timeouts, generic cookie banners, etc.)
    # live OUTSIDE any single artifact -- see recovery.py -- since they are
    # properties of the target application, not of any one recorded task.

    revision_history: list[RevisionMetadata] = []

    created_at: datetime = Field(default_factory=datetime.utcnow)