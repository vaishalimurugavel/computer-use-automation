# Computer-Use Automation System

Discover once with an LLM agent, replay deterministically forever.

This project explores a general approach to computer-use automation: an
LLM agent explores a UI to complete a task once, and that successful run
is recorded as a reusable, versioned **capability artifact**. Future
requests for the same task are served by **replaying the artifact
directly** — no LLM call needed — with typed error handling, risk-gated
human escalation for irreversible actions, and a feedback loop where
human-resolved edge cases make the artifact smarter over time.

Target application(s) for this project: [SauceDemo](https://www.saucedemo.com/)
and [Demoblaze](https://www.demoblaze.com/), two automation-friendly demo
e-commerce sites.

## Status

This project is under active development. The sections below reflect
what's actually built and tested today, not the full end-state design.
See `docs/REPORT.md` (coming soon) for the complete design write-up,
including sections not yet implemented.

## What's built so far

### `schema.py` — the artifact data model
Defines the shape of a recorded **`Capability`**: an ordered list of
`Step`s, each targeting a UI element via one or more `Locator`s (ordered
by preference — e.g. accessibility role first, CSS selector as fallback).
Also defines:
- **`RiskLevel`** — a static, per-step classification (`SAFE` / `RISKY`)
  assigned at recording time, plus an optional `dynamic_risk_check` for
  value-dependent risk (e.g. a transfer amount over a threshold).
- **`ExpectedBusinessOutcome`** — pre-registered, named "this is a known
  non-error result" signatures (e.g. "member not found"), so replay never
  has to guess or re-invoke an LLM to recognize an expected outcome.
- **`RecoveryRule`** — a trigger + recovery action pair for conditions the
  system can handle automatically (e.g. dismissing a known dialog).
- **`RevisionMetadata`** — versioning support for the artifact-evolution
  feedback loop (see `replay.py` below).

All models are Pydantic `BaseModel`s, giving automatic validation (e.g.
`Locator.confidence` is bounded to `[0.0, 1.0]`) and JSON
serialization for free, since artifacts need to be persisted to disk and
reloaded.

**Tests:** `tests/test_schema.py` (11 tests) — covers optional-field
behavior, risk defaults, required-field validation, and correct
construction of nested business-outcome and recovery-rule collections.

### `recovery.py` — global (app-wide) recovery rules
Some recoverable conditions (a session-timeout dialog, a cookie-consent
banner) are properties of the **target application itself**, not of any
one recorded task — they can interrupt any capability at any point. This
module holds `GLOBAL_RECOVERY_RULES`, checked automatically for every
step of every capability, separately from the `local_recovery_rules` that
live on an individual `Capability` for genuinely task-specific quirks.

`find_matching_recovery_rule()` checks global rules first, then falls
back to local rules — global rules take precedence by design, so a single
fix to app-wide recovery logic benefits every capability immediately,
without needing to update each artifact individually. If nothing matches,
it returns `None` rather than guessing.

**Tests:** `tests/test_recovery.py` (6 tests) — covers global-rule
matching, local-rule fallback, precedence ordering (global wins even when
a local rule would also match), and the "no match, no guess" default.

### `replay.py` — the deterministic replay engine
`replay_capability()` executes a `Capability`'s steps in order, without
any LLM involvement, implementing the full outcome-handling logic:

1. **Risk gating** — before executing a step marked `RISKY`, escalate to
   a human for confirmation. Approved → proceed; denied → stop and report.
2. **Execution** — delegated to an injected `StepExecutor` (see below).
3. **Outcome classification** — every result is classified into one of:
   `SUCCESS`, `BUSINESS_OUTCOME` (matches a pre-registered signature),
   `RECOVERABLE` (matches a global or local recovery rule), or
   `HARD_FAILURE` (matches nothing — the safe, conservative default).
4. **Handling** — `SUCCESS` continues; `BUSINESS_OUTCOME` stops and
   reports (not a failure); `RECOVERABLE` continues automatically;
   `HARD_FAILURE` escalates to a human, who can either resolve it
   (replay continues) or not (replay stops).

**The executor "seam":** `StepExecutor` is defined as a `Protocol`
(structural interface), not a concrete class. This decouples "how do we
perceive/act on the page" from "the recorded-flow control logic." All
current tests use a `FakeStepExecutor` that returns scripted results —
no browser dependency at all — so the decision-making logic is fully
tested in isolation before any real Playwright integration is added.

**Tests:** `tests/test_replay.py` (8 tests) — covers the all-success
path, both risk-gating outcomes (approved/denied), business-outcome
recognition, recoverable conditions (both global and local), and
hard-failure handling (both escalated-and-stopped and human-resolved).

## Running the tests

```bash
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
pip install -e .
pytest -v
```

All 25 tests currently pass.

## Not yet built

- **`PlaywrightStepExecutor`** — a real `StepExecutor` implementation
  driving an actual browser against SauceDemo/Demoblaze.
- **Agent/discovery loop** — the LLM-driven side that explores a UI and
  produces a `Capability` artifact from a successful run.
- **A more complete `escalation.py`** — real human-handoff mechanics
  (live session pause/resume), beyond the current test fakes.
- **The design write-up** (`docs/REPORT.md`), including the sections on
  heterogeneity/multi-tenant reuse and what was deliberately cut and why.

## Design decisions worth noting

- **Risk is two-layered**: a static classification assigned once during
  recording (based on the action's inherent nature) plus an optional
  dynamic check evaluated against real parameter values at replay time.
- **Unrecognized states always default to `HARD_FAILURE`**, never to a
  silently-assumed success or business outcome — the system never guesses.
- **Human resolutions are designed to update the artifact itself**
  (via `RevisionMetadata`, versioned rather than overwritten), so a
  once-unrecognized case doesn't require human review again in future
  replays of the same capability.