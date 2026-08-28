"""
Global recovery rules.

These are recoverable conditions that are properties of the TARGET
APPLICATION itself (e.g. a session-timeout dialog, a generic cookie-consent
banner) rather than properties of any single recorded capability.

This is deliberately separate from Capability.local_recovery_rules:
- Global rules are checked on every step, for every capability, automatically.
- A fix to a global rule (e.g. the session-timeout dialog's text changed)
  benefits every capability immediately, with no per-artifact update needed.
- Local rules stay scoped to the one capability where they're genuinely
  task-specific, so they don't pollute this shared, app-wide list.

See schema.py's RecoveryRule for the shared data shape.
"""

from __future__ import annotations

from capability_recorder.schema import RecoverableConditionType, RecoveryRule

# ---------------------------------------------------------------------------
# The global rule set for the target application(s) used in this project
# (SauceDemo / Demoblaze). In a real multi-tenant system this would likely
# be keyed by target_app, since "global" here really means "global to one
# application," not literally universal across every possible target.
# ---------------------------------------------------------------------------

GLOBAL_RECOVERY_RULES: list[RecoveryRule] = [
    RecoveryRule(
        trigger="text_contains:session has expired",
        condition_type=RecoverableConditionType.SESSION_TIMEOUT_REAUTH,
        recovery_action="reauth_and_retry",
    ),
    RecoveryRule(
        trigger="role:button;name:Accept Cookies",
        condition_type=RecoverableConditionType.KNOWN_DIALOG_DISMISS,
        recovery_action="dismiss_dialog",
    ),
    RecoveryRule(
        trigger="network_error:timeout",
        condition_type=RecoverableConditionType.TRANSIENT_RETRY,
        recovery_action="retry_once_after_delay",
    ),
]


def find_matching_recovery_rule(
    observed_state: str,
    local_rules: list[RecoveryRule] | None = None,
) -> RecoveryRule | None:
    """Check global rules first, then any capability-specific local rules.

    `observed_state` is a simple string signal for now (e.g. page text, or a
    structured marker like "network_error:timeout") -- the actual matching
    logic (does this trigger apply to this observed_state) lives in the
    replay engine, which understands the concrete trigger syntax
    (text_contains:, role:;name:, network_error:). This function only
    decides *ordering and scope*: global rules take precedence, since they
    represent infrastructure-level knowledge; local rules are consulted
    only if no global rule matches.
    """
    local_rules = local_rules or []
    for rule in GLOBAL_RECOVERY_RULES:
        if _trigger_matches(rule.trigger, observed_state):
            return rule
    for rule in local_rules:
        if _trigger_matches(rule.trigger, observed_state):
            return rule
    return None


def _trigger_matches(trigger: str, observed_state: str) -> bool:
    """Minimal matcher for now: exact substring match on the trigger's
    payload after its type prefix. This is intentionally simple -- it will
    be replaced/extended once the replay engine's real observation format
    (accessibility tree snapshot, DOM query result) is wired up.
    """
    if ":" not in trigger:
        return trigger in observed_state
    _prefix, payload = trigger.split(":", 1)
    return payload.lower() in observed_state.lower()