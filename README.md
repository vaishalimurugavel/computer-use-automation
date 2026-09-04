# Computer-Use Automation System

Discover once with an LLM agent, replay deterministically forever.

An LLM agent explores a real web UI to accomplish a goal, and that
successful run is recorded as a reusable, versioned **capability
artifact**. Future requests for the same task are served by **replaying
the artifact directly** — with zero LLM calls — using typed error
handling, redacted secrets, an enforced allowlist, and risk-gated human
escalation for irreversible or unrecognized situations.

**For the full design write-up** — architecture rationale, schema
design, error taxonomy, safety model, escalation/handoff design, and an
honest account of what was cut and why — see [`REPORT.md`](REPORT.md).

**For proof the core claim actually works** — a saved artifact plus logs
from a real discovery run and two real replay runs (one clean success,
one that hit and correctly handled a genuine failure) — see
[`/evidence`](evidence/).

## Quick start

```bash
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
pip install -e .
playwright install chromium  # downloads the browser binary; a separate step from pip install
pytest -v                    # all 85 tests should pass
```

To run the live scripts (discovery/replay against a real browser), set
your Gemini API key as an environment variable first — never hardcode it
in source, since this repo is public:

```powershell
$env:GEMINI_API_KEY = "your-key-here"
python manual_test_discovery.py   # LLM-driven: explores SauceDemo, records a Capability
python manual_test_replay.py      # LLM-free: replays the saved artifact from artifacts/example_capability.json
```

## Module overview

### `schema.py` — the artifact data model
Defines `Capability`: an ordered list of `Step`s, each targeting a UI
element via one or more `Locator`s (ordered by preference — accessibility
role+name first, with room for CSS/text-content fallback). Also defines
`RiskLevel` (static risk classification per step), `ExpectedBusinessOutcome`
(pre-registered "this is a known non-error result" signatures),
`RecoveryRule`, `InputParam`/`OutputField` (the capability's typed
contract), and `RevisionMetadata` (versioning for the artifact-evolution
feedback loop). All models are Pydantic `BaseModel`s, giving automatic
validation and JSON serialization for free.

### `recovery.py` — global (app-wide) recovery rules
Holds `GLOBAL_RECOVERY_RULES` — conditions that are properties of the
target application itself (a session-timeout dialog, a cookie banner),
checked automatically for every capability, separately from a
capability's own `local_recovery_rules` for genuinely task-specific
quirks. Global rules take precedence by design.

### `perception.py` — turning a live page into a structured observation
`flatten_accessibility_tree()` parses Playwright's `aria_snapshot()`
output (YAML-style text) into a flat, numbered list of interactive
elements only. Pure function, no browser dependency — fully unit tested
with fake fixtures. `capture_observation()` is the thin, live-only
wrapper that actually calls Playwright.

### `llm_client.py` — asking the model what to do next
Builds a plain-text prompt (goal, numbered element list, action history)
instructing the model to respond in JSON only, and defensively parses the
response (handling markdown-fenced or malformed output) into a structured
`AgentDecision`. Uses the `google-genai` SDK.

### `agent.py` — the LLM-driven discovery loop
`discover_capability()` runs the actual loop: observe → ask the LLM for
the next action → execute it on a real page → record it as a `Step` →
repeat until the LLM reports done. **Secret redaction**: when a sensitive
field is detected (`is_sensitive_field()` — password, SSN, PIN, etc.), the
literal typed value is used to actually drive the page (`execution_step`)
but never written into the saved artifact (`artifact_step`, which stores
a `{{placeholder}}` instead, plus a matching `InputParam`).

### `replay.py` — the deterministic, LLM-free replay engine
`replay_capability()` executes a `Capability`'s steps in order against an
injected `StepExecutor`, classifying every result into one of four
outcomes (`SUCCESS`, `BUSINESS_OUTCOME`, `RECOVERABLE`, `HARD_FAILURE` —
unrecognized states always default to `HARD_FAILURE` and escalate, never
a silent guess). Also: resolves `{{placeholder}}` values from a supplied
`input_values` dict, verifies `success_condition` against the final page
state after all steps complete, and enforces an optional `Allowlist`
before any step executes.

### `safety.py` — allowlist enforcement
`check_allowlist()` is a pure policy check: every step's action type must
be permitted, and `NAVIGATE` steps' target domains must be in the
configured allowlist. A violation is treated as `HARD_FAILURE`, routed
through the same escalation path as any other failure.

### `escalation.py` — human-in-the-loop handlers
`ConsoleEscalationHandler` (simple approve/deny prompt), and
`LiveSessionEscalationHandler` (the fuller model: pauses and lets a human
interact with the *same* live, already-authenticated browser session
before resuming replay on that same session — no new login, no lost
state). `NullEscalationHandler` is a fail-safe default that always denies
without prompting, for headless contexts with no human available.

### `executor.py` — the real Playwright implementation
`PlaywrightStepExecutor` implements `StepExecutor` against a live page.
Unlike discovery-time execution (which raises on failure), this never
raises — every error is caught and turned into a failed `ExecutionResult`
for `replay.py` to classify.

## Testing

```bash
pytest -v
```

All 85 tests pass. Nearly everything is unit tested with fakes (no
browser, no network, no LLM call needed) — the few genuinely
Playwright/API-dependent functions (`capture_observation`,
`call_gemini`, `PlaywrightStepExecutor`, `discover_capability`) are
instead exercised via the real, live runs saved in `/evidence`.

## A note on live-caught bugs

Three real API/SDK deprecations were hit and fixed during this project —
Playwright's `page.accessibility.snapshot()` removal, the full
deprecation of `google-generativeai`, and two rounds of Gemini model
retirement — none of which the unit test suite could have caught on its
own, since its fixtures encoded assumptions that had already gone stale.
See `REPORT.md`'s closing note for details. This is part of why the
project treats live verification as a first-class part of the process,
not an afterthought.