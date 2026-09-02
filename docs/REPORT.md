# Computer-Use Automation System — Design Report

## 1. What this is

An LLM agent explores a real web UI once to accomplish a goal, and that
successful run is recorded as a reusable, versioned **capability
artifact**. Future requests for the same task are served by **replaying
the artifact directly** — with zero LLM calls — using typed error
handling, risk-gated human escalation for irreversible actions, and a
feedback loop where human-resolved edge cases can update the artifact
itself.

Target application for the working implementation: [SauceDemo](https://www.saucedemo.com/),
an automation-friendly demo e-commerce site (chosen specifically because
automation is explicitly welcomed there, unlike a real commercial site).

**The core claim is demonstrated live, not just designed on paper.** A
discovery run against SauceDemo (goal: log in, add the Sauce Labs
Backpack to cart, view the cart) took ~6 LLM calls and produced a 5-step
`Capability`. Replaying that exact artifact afterward — via
`manual_test_replay.py` — completed all 5 steps successfully with **zero
LLM calls**. That result (screenshots/output in `artifacts/`) is the
central piece of evidence for this whole project: the system does what
it claims, not just in theory.

## 2. Architecture overview

![Architecture diagram](architecture.png)

**The seam** (per the assignment's own framing) is the `StepExecutor`
protocol in `replay.py`: it defines *what* an executor must do (`execute`,
`observe_current_state`) without saying *how*. `replay.py`'s entire
control-flow logic — risk gating, outcome classification, recovery
lookup — was built and fully unit-tested against a `FakeStepExecutor`
that never touches a browser. When `PlaywrightStepExecutor` was written
later and swapped in for a live run, **zero lines of `replay.py` changed**.
That's not a design claim — it's a fact confirmed by running both.

## 3. The artifact schema

A `Capability` (in `schema.py`) is the recorded, reusable unit:

- **`steps: list[Step]`** — an ordered, flat list (no branching inside the
  artifact itself; branching/recovery logic lives in the replay engine,
  not the data format — see §5).
- **`ElementTarget` / `Locator`** — each step's target carries an
  *ordered list* of locator strategies (accessibility role+name first,
  with room for CSS-selector or text-content fallback), each with a
  `confidence` score and an optional `reasoning` string explaining *why*
  that confidence was assigned (e.g. "no accessible name present"). This
  directly answers the "how is each element identified, with reasoning
  about robustness" requirement — it's not a single brittle selector, and
  the reasoning is inspectable by a human reviewer, not just a number.
- **`expected_business_outcomes`** — pre-registered, named signatures
  (e.g. "text contains 'No member found' → NOT_FOUND") so replay never
  has to guess or re-invoke an LLM to recognize a known non-error result.
- **`local_recovery_rules`** vs. **global rules** (`recovery.py`) — a
  deliberate split: conditions that are properties of the *target
  application itself* (a session-timeout dialog, a cookie banner) live in
  one shared, engine-level list, checked automatically for every
  capability. Conditions genuinely specific to one task stay scoped to
  that task's artifact. This was a real design decision, not a default:
  the alternative (declaring every recovery condition per-artifact) was
  rejected specifically because a single app-wide fix would otherwise
  need to be duplicated across every capability that could encounter it.
- **`RiskLevel` (static) + `dynamic_risk_check` (optional)** — risk is
  two-layered. A static classification is assigned once, based on the
  *inherent nature* of the action (does this step create/submit/commit
  something irreversible) — this doesn't change across invocations. An
  optional dynamic check can be evaluated against the *actual parameter
  values* of a specific invocation (e.g. a transfer step could be
  statically `SAFE` but carry a rule like `amount > 1000` that escalates
  only when the real value crosses a threshold). **Status: static
  `risk_level` is implemented and enforced in `replay.py`;
  `dynamic_risk_check` is defined in the schema but not yet evaluated
  anywhere — see §7, Cuts.**
- **`revision_history` / `version`** — supports the human-resolution
  feedback loop (§6): when a human resolves a previously-unrecognized
  state, the artifact can gain a new signature as a **new version**,
  never an in-place overwrite, so the change is auditable and reversible
  — the same reasoning that motivates journaling in file systems and
  write-ahead logs in databases, applied to artifact evolution.

## 4. The error/outcome taxonomy (§3.3 in the assignment)

Every step's result is classified into exactly one of four categories
(`replay.py::_classify_outcome`):

| Category | Meaning | Escalates? | Continues replay? |
|---|---|---|---|
| `SUCCESS` | Expected outcome | No | Yes |
| `BUSINESS_OUTCOME` | Known, named non-error result (matched a pre-registered signature) | No | No — returns as a final result, not a failure |
| `RECOVERABLE` | Matched a global or local recovery rule | No | Yes, automatically |
| `HARD_FAILURE` | Matched nothing | **Yes** | Only if a human resolves it |

The check order matters: business outcomes are checked *before* recovery
rules, since a pre-registered business signature is a more specific,
higher-confidence match than a generic recovery trigger. **The critical
design principle, applied consistently throughout**: an unrecognized
state never defaults to a guessed success or a silently-assumed benign
outcome. It defaults to `HARD_FAILURE` and escalates. This is the same
posture as "verification over vibes" applied structurally, not just as a
value statement.

## 5. Safety (§3.4)

Risk gating happens in `replay.py`, *before* a step executes:

 if step.risk_level == RISKY:
approved = escalation_handler.escalate(step, reason="risky action requires confirmation")
if not approved: stop and report ESCALATED 

This is independent of whether execution would have succeeded — a step
can be technically fine to execute and still require human confirmation
purely because of *what it is*. Static risk is assigned once at recording
time based on action type (e.g. a final "Confirm and Create" submit is
inherently riskier than a search field); no dynamic, value-based
escalation is implemented yet (see §7).

## 6. Human-in-the-loop escalation (§3.6) and the learning loop

Escalation is triggered by two independent conditions — a risky step
(before execution) or a hard failure (after execution) — both routed
through the same `EscalationHandler.escalate(step, reason)` call, with
the reason recorded so it's clear afterward *why* a human was asked, not
just *that* they were.

**The learning-loop design** (not yet implemented, but deliberately
designed for): when a human resolves a previously-unrecognized state,
that resolution should be able to add a new `ExpectedBusinessOutcome` or
`RecoveryRule` to the artifact, incrementing its version. Two decisions
were made deliberately here:
- **No second-reviewer approval required** for the update itself — the
  artifact's *original* steps were already human-approved at recording
  time, so one qualified human resolving a narrow, specific new case is
  treated as sufficient.
- **Every update is versioned, never overwritten in place** — the same
  reasoning as file-system journaling: if a bad classification is added
  later, it should be visible and reversible, not silently baked in.

## 7. Heterogeneity & multi-tenant reuse (§3.7) — design only

Not implemented against a second target, but reasoned through
explicitly, using SauceDemo and Demoblaze as stand-ins for "two tenants
running similar-but-differently-built versions of the same app type":

- **The seam** is exactly the `StepExecutor` boundary already built:
  "how we perceive/act on a surface" (Playwright + accessibility tree)
  is fully decoupled from "the recorded flow" (the `Capability` artifact
  itself, which references only abstract locator strategies, never a
  Playwright-specific API call).
- **A real limitation was found, live, not hypothesized**: SauceDemo has
  six products, each with an identically-named "Add to cart" button.
  `get_by_role(name="Add to cart")` legitimately matches all six, and
  Playwright's strict mode refused to click an ambiguous locator. This is
  concrete evidence that accessible-name-only identification is
  insufficient even on a well-structured target — a real generalization
  problem, not a theoretical one. **Current fix: fall back to `.first`
  when multiple elements match** — this unblocks execution but is
  correct only by DOM-order coincidence, not real disambiguation. The
  correct fix — extending `perception.py` to surface a secondary,
  disambiguating signal (a `data-test`/`id` attribute, or nearby
  product-name context) when accessible names collide — is a concrete,
  scoped next step, not a vague aspiration.
- **For legacy/desktop targets** (mentioned in the assignment as the real
  environment): Playwright itself only automates browsers. A native
  desktop target would need a different executor entirely (OS-level
  automation, e.g. `pyautogui` or Windows UI Automation) — but because of
  the `StepExecutor` seam, this would be a *new implementation of the
  same interface*, not a redesign of `replay.py`, `schema.py`, or the
  outcome taxonomy.

## 8. What was cut, and why

Being specific about what's missing and why is more useful than
pretending the system is more complete than it is:

- **Dynamic, value-based risk checks** (`Step.dynamic_risk_check`) — the
  field exists in the schema, but nothing in `replay.py` evaluates it yet
  against real invocation parameters. Cut for time; the static risk-gate
  path was prioritized since it demonstrates the core mechanism.
- **Robust element disambiguation** — the `.first` fallback (§7) is a
  known, documented shortcut, not a real fix.
- **The human-resolution → artifact-update feedback loop** — fully
  designed (§6), including the versioning/audit-trail reasoning, but not
  wired into working code. Implementing it needs a real interface for a
  human to review a `HARD_FAILURE`'s captured `observed_state` and
  produce a new signature — a UI/interaction problem as much as a code
  one, and the assignment explicitly allows mocking this layer.
- **Second live target (Demoblaze)** — used only as a design reference,
  not actually built against, per the deliberate "depth over breadth"
  call made early in the project.
- **Screenshot-based perception** — `perception.py` is text-only
  (accessibility tree), a reasonable choice for SauceDemo/Demoblaze
  specifically, but a real limitation for messier legacy UIs with poor
  accessibility metadata, where vision-based fallback would matter more.

## 9. What I'd build next, in priority order

1. Fix element disambiguation properly (secondary locator signal beyond
   role+name) — this is the single highest-value fix, since it's a
   concrete, already-identified bug, not a speculative improvement.
2. Wire up the human-resolution → new-artifact-version feedback loop —
   the mechanism with the most leverage for making the system genuinely
   self-improving over time, per the assignment's own framing of
   long-term reuse.
3. Evaluate `dynamic_risk_check` at replay time.
4. A second live target, to convert the multi-tenant discussion in §7
   from reasoned-through to empirically demonstrated.

## 10. A note on environment drift, encountered directly

Across this project, three separate pieces of tooling changed out from
under the initial design, each caught only through live verification,
never through the unit test suite (which used fixtures encoding
assumptions that had already gone stale):
- Playwright removed `page.accessibility.snapshot()` in favor of
  `aria_snapshot()`, which returns YAML-style text instead of a nested
  dict — `perception.py` had to be rewritten, and its tests rebuilt
  around a real captured snapshot rather than a guessed format.
- The `google-generativeai` Python package was fully deprecated in favor
  of `google-genai`.
- Two rounds of Gemini model retirement (`gemini-1.5-flash`, then
  `gemini-2.5-flash`) surfaced as live 404s, with the API's own error
  response directing the correct current model name.

This is, in miniature, exactly the argument for why deterministic replay
and live verification both matter: a static design doc would have
described a system that no longer matched reality in three separate
places, and only running it — repeatedly, against the real target and
real APIs — surfaced that.