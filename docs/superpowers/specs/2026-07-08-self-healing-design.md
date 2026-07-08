# DOM-grounded self-healing of failed test cases — design

**Date:** 2026-07-08
**Status:** approved, ready for implementation planning
**Builds on:** the existing "Mark as expected behavior" flow and [2026-07-08-generation-improvements-design.md](2026-07-08-generation-improvements-design.md)

## Problem

When an auto-executed test fails but the app's *actual* behavior (what the user sees in the run screenshot) is correct, the test case and its generated code are stale and should be updated to match reality. Today's "Mark as expected behavior" flow rewrites only the `expected_result` wording and can *blindly* regenerate the Playwright code (no knowledge of the real page), so a wrong selector/assertion often stays wrong and the re-run fails again.

## Goal

Let a user confirm "the observed behavior is correct" on a failed run and have the app heal **both** the `expected_result` and the Playwright **code**, grounded in the **actual failed page** (its DOM/accessibility snapshot), then re-run to confirm it now passes — with the user previewing and approving the changes first.

## Scope

**In scope:**
- Capture a compact DOM/accessibility snapshot of the page at failure time (alongside the existing screenshot + page-text).
- A heal endpoint that rewrites `expected_result` AND the code from the observed snapshot.
- Extend the existing "Update Expected Result" modal with an editable proposed-code panel; preview → approve → save both → re-run.
- Graceful fallback to today's text-only behavior when no snapshot exists.

**Out of scope:** rewriting the manual test-case *steps* (only expected_result + code); multi-iteration auto-heal loops (one heal per click; user re-clicks to iterate); healing on `passed` runs.

## Decisions (from brainstorming)
- Heal **expected_result + code**, DOM-grounded.
- Capture the page **at failure time** (truthful state, not a re-navigation).
- **Preview + edit + approve**, then save + re-run (no silent auto-apply).

## Behavior & data flow

```
run fails → result carries page_snapshot → user clicks "Mark as expected behavior"
  → POST /heal { current_code, page_snapshot, error_message }
  → { suggested_expected, suggested_code }
  → user reviews/edits both in the modal → Save
  → PATCH expected_result  +  save-playwright(code)  +  re-run
  → shows pass/fail of the confirming re-run
```

### A. Capture at failure time (runner)
In `backend/services/playwright_runner_wrapper.py.tmpl`, in the failed/error branches that already call `_capture_screenshot` + `_page_context`, also capture a **compact snapshot**: `page.accessibility.snapshot()` reduced to interactive/labeled nodes (role, name, and value/placeholder where present), serialized to a string and **hard-capped** (e.g. 6000 chars) to bound payload/prompt size. Add it to the result dict as `page_snapshot` (str, `""` when unavailable).

`run_playwright_code` passes it through; `RunResponse` gains `page_snapshot: str = ""`. It is returned to the client with the run result (the client already routes the run result into the heal modal). Not persisted server-side — no DB growth.

### B. Heal endpoint (backend)
`POST /api/projects/{pid}/test-cases/{tcid}/heal`, body:
```json
{ "current_code": "<code>", "page_snapshot": "<snapshot>", "error_message": "<err>" }
```
Returns `{ "suggested_expected": "<text>", "suggested_code": "<python>" }`. Does **not** persist (mirrors `suggest-expected-result`). Implemented via a new `llm_service.heal_test_case(tc_dict, current_code, page_snapshot, error_message, settings, ...)` and a new prompt `build_heal_prompt(...)` + `HEAL_SYSTEM_PROMPT`. The system prompt directs: *the observed page is the correct behavior*; rewrite `expected_result` to describe what the snapshot shows, and fix the Playwright code's **selectors/assertions to match the snapshot**, preserving the original navigation/step intent and the existing signature (2-arg or 4-arg). Output must be a JSON object with `suggested_expected` and `suggested_code`.

If `page_snapshot` is empty, fall back to the current text-only path: reuse `suggest_expected_result` for the wording and `generate_playwright_code` (blind) for the code, so the endpoint still returns both fields.

### C. Frontend (extend the "Update Expected Result" modal)
- Add a second editable panel, **"Proposed code"**, beside the existing expected-result textarea in `adaptExpectedModal`.
- `openAdaptExpectedModal(runResult)` now calls `/heal` (with `runResult.page_snapshot`, the current code, and the error) and populates BOTH the suggested expected and suggested code panels.
- "Try another phrasing" re-calls `/heal`.
- **Save** → PATCH `expected_result` (existing endpoint) + `save-playwright` (existing endpoint, with the edited code) + re-run via `run-playwright`; render the confirming run's pass/fail.
- Remove the "Regenerate Playwright code from new expected" checkbox — code is always healed now.
- Keep the modal titled around "confirm observed behavior"; the button stays "✓ Mark as expected behavior" and only shows on FAILED runs (unchanged).

### D. Error handling
- No `page_snapshot` → text-only fallback (above); the modal still works.
- `/heal` LLM failure → surface the error in the modal; change nothing (no PATCH, no save).
- Re-run after save reports pass/fail as today; if still failing, a note suggests manual edit (existing behavior).

## Data model
No schema change. `page_snapshot` is transient (run response only). Reuses `expected_result` (PATCH) and `playwright_code` (save-playwright) storage.

## API
- `RunResponse` gains `page_snapshot: str = ""`.
- New `POST /test-cases/{tcid}/heal` → `{ suggested_expected, suggested_code }`.
- `suggest-expected-result` remains (used by the text-only fallback).

## Testing
- **Runner:** a failing test against the local HTML form yields a non-empty `page_snapshot` in the result.
- **Prompt (deterministic):** `build_heal_prompt(tc, code, snapshot, err)` includes the snapshot, the current code, and the "observed page is correct" directive; requests JSON with both keys.
- **Heal endpoint:** returns both `suggested_expected` and `suggested_code`; empty-snapshot path falls back and still returns both (mock the LLM layer).
- **Integration (real self-heal loop):** seed a test whose code asserts something false about a local page → run (fails, captures snapshot) → heal (stub the LLM to return a corrected assertion + expected) → save (PATCH + save-playwright) → re-run → **passes**. Uses the isolated `tests/routers/conftest.py` + `_data_dir` monkeypatch; no real DB / no real network.

## Security
Unchanged: the healed code is user-reviewable and runs through the same denylist/sandbox as any auto-execute code; credentials still never enter generated/healed code (injection stays runtime-only). The DOM snapshot contains page structure only; it is transient and never persisted.
