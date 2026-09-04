"""
Allowlist enforcement -- an explicit, configurable safety policy.

The assignment explicitly requires enforcing an allowlist of permitted
domains/routes and allowed action types, so the agent/replay engine
cannot act outside it. This module is a pure, testable policy check
(no Playwright dependency), in the same spirit as recovery.py: a
cross-cutting rule set that replay.py consults before acting, rather
than logic baked directly into the executor.

Scope of this MVP: action-type checking applies to every step. Domain
checking applies to NAVIGATE steps specifically, since the target URL is
known directly from step.value without needing to ask a live page what
URL it's currently on. Extending domain checking to CLICK/TYPE steps
(verifying the *current* page's domain, not just navigation targets)
would need the executor to report its current URL -- a natural next
step, not implemented here. See REPORT.md "Cuts".
"""

from __future__ import annotations

from urllib.parse import urlparse

from pydantic import BaseModel

from capability_recorder.schema import ActionType, Step

DEFAULT_ALLOWED_ACTIONS = [
    ActionType.CLICK,
    ActionType.TYPE,
    ActionType.NAVIGATE,
    ActionType.EXTRACT,
    ActionType.WAIT_FOR,
]


class Allowlist(BaseModel):
    """Explicit, configurable safety policy. Constructed once per target
    application/deployment (e.g. `Allowlist(allowed_domains=["saucedemo.com"])`)
    and passed into replay_capability -- there is no implicit, permissive
    default domain list; a Capability with no supplied Allowlist is not
    policy-checked at all (see replay.py -- allowlist is optional), which
    is itself worth being explicit about rather than silently assuming
    permissive behavior is safe.
    """

    allowed_domains: list[str]
    allowed_actions: list[ActionType] = DEFAULT_ALLOWED_ACTIONS


def check_allowlist(step: Step, allowlist: Allowlist) -> str | None:
    """Return None if the step is permitted, or a human-readable
    violation reason if not. Never raises -- callers (replay.py) decide
    how to handle a violation (currently: treat as HARD_FAILURE, which
    escalates through the existing outcome taxonomy rather than needing
    a separate handling path).
    """
    if step.action not in allowlist.allowed_actions:
        allowed = [a.value for a in allowlist.allowed_actions]
        return f"Action type {step.action.value!r} is not permitted by the allowlist (allowed: {allowed})"

    if step.action == ActionType.NAVIGATE and step.value:
        domain = urlparse(step.value).netloc.lower()
        if not domain:
            return f"Could not parse a domain from navigation target {step.value!r}"
        permitted = any(
            domain == allowed_domain.lower() or domain.endswith("." + allowed_domain.lower())
            for allowed_domain in allowlist.allowed_domains
        )
        if not permitted:
            return (
                f"Navigation target domain {domain!r} is not in the allowed domains "
                f"{allowlist.allowed_domains}"
            )

    return None