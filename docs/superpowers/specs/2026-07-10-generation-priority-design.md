# Generation prompt — prioritize core workflows — design

**Date:** 2026-07-10
**Status:** approved, ready for implementation planning

## Problem

The test-case generation prompt over-emphasizes visual grounding (exact px, hex,
fonts, component variants). Given a screenshot (e.g. a login page), the model floods
the output with cosmetic UI-verification tests and buries — or omits — the critical
functional flows (valid login, invalid login). The existing "CRITICAL PATH FIRST"
rule only asks for *ordering*, which the visual emphasis overwhelms. Result: the
tool's core purpose (surface the important test cases fast) is not served.

## Goal

Generation must **guarantee and front-load the feature's core functional workflows**,
then fill the rest of the requested count with secondary (edge/UI/cosmetic) tests.

## Scope

Wording-only changes to `backend/prompts/templates.py`:
- `SYSTEM_PROMPT` (the shared system prompt), and
- the rules list in `build_generation_user_message`, mirrored in
  `build_iterate_user_message`.

**Out of scope:** no new fields, endpoints, schema, or code paths. Reuse the existing
`min_test_cases` and `preferred_test_types`.

## Design

### 1. Core-workflow-first guarantee (replaces ordering-only rule)
Replace the "CRITICAL PATH FIRST" rule with a guarantee:
- First identify the feature's **core functional workflows**: the primary happy path
  AND its main negative/failure paths — the flows a QA lead marks "must pass before
  release."
- Emit **all** core workflows as the **first** test cases, in priority order, **before
  any** cosmetic / UI-detail / edge test.
- Core workflows are **mandatory even when the requested count is small**: if the count
  is smaller than the number of core workflows, emit the core workflows and stop.
- Only after all core workflows are covered may secondary tests (edge cases, UI/visual
  verification, minor variations) be added to reach the count.
- Bake in a concrete example: login → `[valid credentials → lands on post-login state;
  invalid password → error shown; empty required fields → validation]` come first;
  button color / logo / font checks are secondary.

### 2. Rebalance visual grounding
Reframe the visual-precision emphasis: exact element names / px / hex / fonts govern
**how each test is written** (stay grounded; no generic tests) — **not what gets
prioritized**. State explicitly that pure-cosmetic / visual-only tests (color, size,
font, static-text-present) are **secondary** and must never displace functional
workflows. Keep the "no generic test cases" grounding requirement intact.

### 3. Count interaction
Make explicit: `min_test_cases` is the target for the **total** number of tests; core
workflows come first and count toward it; secondary tests fill the remainder. Do not
pad with low-value cosmetic tests to hit the count — prefer additional negative/edge
variations of the core flow over cosmetic checks.

### 4. preferred_test_types filter
Keep the existing "within the filter, most-critical first" behavior; align its wording
with the new core-first framing (the first emitted test of the filtered type must be
the most critical of that type for the feature).

## Testing

Prompt strings produce non-deterministic LLM output, so:
- **Automated (deterministic):** assert `build_generation_user_message(...)` (and the
  iterate builder) include the core-workflow-first guarantee text, the
  cosmetic-secondary wording, and the count-interaction wording. Assert the login
  example phrasing is present.
- **Manual (author-run) smoke:** feed a login-page screenshot to `/api/generate` and
  confirm the first 2–3 generated cases are the functional login flows (valid/invalid),
  not cosmetic checks. Not an automated test (LLM-dependent).

## Risks / notes
- Reduced cosmetic coverage is intended, not a regression: cosmetic tests still appear
  when the count is large enough, just after the core flows.
- No behavior change to storage, dedup, or the API contract.
