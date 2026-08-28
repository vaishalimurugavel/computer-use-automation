"""
Tests for the global recovery rules module.

Key behaviors under test:
- Global rules match known app-wide conditions (session timeout, cookie banner)
- Global rules are checked BEFORE local rules (global takes precedence)
- A local rule can still match when nothing global applies
- No match returns None, rather than raising or guessing
"""

from capability_recorder.recovery import GLOBAL_RECOVERY_RULES, find_matching_recovery_rule
from capability_recorder.schema import RecoverableConditionType, RecoveryRule


def test_global_rule_matches_session_timeout_text():
    rule = find_matching_recovery_rule("Your session has expired. Please log in again.")
    assert rule is not None
    assert rule.condition_type == RecoverableConditionType.SESSION_TIMEOUT_REAUTH


def test_global_rule_matches_transient_network_error():
    rule = find_matching_recovery_rule("network_error:timeout")
    assert rule is not None
    assert rule.condition_type == RecoverableConditionType.TRANSIENT_RETRY


def test_no_match_returns_none_rather_than_guessing():
    rule = find_matching_recovery_rule("Completely unrelated page content")
    assert rule is None


def test_local_rule_matches_when_no_global_rule_applies():
    local_rules = [
        RecoveryRule(
            trigger="text_contains:duplicate account warning",
            condition_type=RecoverableConditionType.KNOWN_DIALOG_DISMISS,
            recovery_action="dismiss_dialog",
        )
    ]
    rule = find_matching_recovery_rule("Warning: duplicate account warning detected", local_rules=local_rules)
    assert rule is not None
    assert rule.recovery_action == "dismiss_dialog"


def test_global_rules_take_precedence_over_local_rules():
    """If a local rule and a global rule could both match, global wins --
    global rules represent infrastructure-level knowledge and should not
    be shadowed by a capability-specific declaration."""
    local_rules = [
        RecoveryRule(
            trigger="text_contains:session has expired",
            condition_type=RecoverableConditionType.KNOWN_DIALOG_DISMISS,  # deliberately different classification
            recovery_action="local_dismiss_instead",
        )
    ]
    rule = find_matching_recovery_rule("Your session has expired.", local_rules=local_rules)
    assert rule is not None
    assert rule.condition_type == RecoverableConditionType.SESSION_TIMEOUT_REAUTH
    assert rule.recovery_action == "reauth_and_retry"


def test_global_recovery_rules_list_is_not_empty():
    assert len(GLOBAL_RECOVERY_RULES) > 0