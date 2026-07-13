# Generation Priority Prompt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Rewrite the test-case generation prompt so it guarantees and front-loads the feature's core functional workflows (happy path + main negative/failure paths), then fills the requested count with secondary (edge/UI/cosmetic) tests.

**Architecture:** Wording-only edits to `backend/prompts/templates.py` — the shared `SYSTEM_PROMPT`, the `build_generation_user_message` rules, and the mirrored `build_iterate_user_message` rules. Deterministic tests assert the new guidance text is present in the built prompts (LLM output itself is non-deterministic and verified by a manual smoke).

**Tech Stack:** Python 3.11, pytest (`asyncio_mode=auto`).

## Global Constraints

- Wording-only: no new fields, endpoints, schema, or code paths. Reuse existing `min_test_cases` / `preferred_test_types`.
- Keep the existing "no generic test cases" grounding requirement and the strict "Verify …" title format — do not weaken them.
- No `Co-Authored-By` trailer. Use the project venv. Run `python -m pytest -q` green before committing.

---

### Task 1: Core-workflow-first generation prompt

**Files:**
- Modify: `backend/prompts/templates.py` (`SYSTEM_PROMPT`; the `CRITICAL PATH FIRST` rule and the "Cover happy paths…" rule in `build_generation_user_message`; the `CRITICAL PATH FIRST` rule in `build_iterate_user_message`)
- Test: `tests/services/test_generation_priority_prompt.py`

**Interfaces:**
- Consumes: existing `build_generation_user_message(parsed, existing, project_description="", target_feature_name="", extra_instruction=None, min_test_cases=None, preferred_test_types=None) -> str` and `build_iterate_user_message(...)`.
- Produces: no new symbols — only the prompt text changes.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_generation_priority_prompt.py
from backend.models.test_case import TestCase
from backend.services.parsers.base import ParsedInput
from backend.prompts.templates import (
    SYSTEM_PROMPT, build_generation_user_message, build_iterate_user_message,
)


def _parsed():
    return ParsedInput(source_type="screenshot", feature_name="Login",
                       raw_context="Login screen with Email, Password, Sign in button")


def test_system_prompt_subordinates_cosmetics_to_workflows():
    s = SYSTEM_PROMPT.lower()
    assert "core functional workflow" in s
    assert "cosmetic" in s and "secondary" in s


def test_generation_prompt_guarantees_core_workflows_first():
    msg = build_generation_user_message(_parsed(), [], min_test_cases=10)
    low = msg.lower()
    # Core-first guarantee + mandatory-even-if-count-small
    assert "core functional workflow" in low
    assert "before any" in low and "cosmetic" in low
    # login example baked in
    assert "valid" in low and "invalid" in low
    # count interaction wording present when a count is given
    assert "fill" in low or "remainder" in low


def test_generation_prompt_small_count_still_lists_core_first():
    # With no count, the guarantee text is still present (not gated on count)
    msg = build_generation_user_message(_parsed(), [])
    assert "core functional workflow" in msg.lower()


def test_iterate_prompt_uses_core_first_framing():
    # signature: build_iterate_user_message(existing, instruction, feature_filter, type_filter, ...)
    msg = build_iterate_user_message([], "add more login tests", None, None)
    assert "core" in msg.lower() and "critical" in msg.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && python -m pytest tests/services/test_generation_priority_prompt.py -v`
Expected: FAIL (current prompts lack "core functional workflow" / cosmetic-secondary wording).

- [ ] **Step 3a: Reframe `SYSTEM_PROMPT`**

In `backend/prompts/templates.py`, replace this exact sentence in `SYSTEM_PROMPT`:

```
    "could apply to any app. Every test case title begins with the word \"Verify\" and "
```
with:

```
    "could apply to any app. Visual precision (element names, px sizes, hex colors, fonts, "
    "component variants) governs HOW you write each test — NOT what you prioritize: a "
    "feature's core functional workflows come first, and purely cosmetic/visual-only checks "
    "are secondary and must never displace them. Every test case title begins with the word \"Verify\" and "
```

- [ ] **Step 3b: Replace the generation `CRITICAL PATH FIRST` rule**

Replace the entire existing rule string (the list item that begins `"- CRITICAL PATH FIRST. Before writing any test case, identify the single most-critical "` and continues through `"Do NOT emit test cases in random order."`) with:

```python
        "- CORE WORKFLOWS FIRST (MANDATORY). Before writing anything, identify this feature's "
        "CORE FUNCTIONAL WORKFLOWS: the primary happy path AND its main negative/failure paths "
        "— the flows a QA lead marks 'must pass before release'. Emit ALL core workflows as the "
        "FIRST test cases, in priority order, BEFORE ANY cosmetic, UI-detail, or edge test. "
        "Core workflows are mandatory even when the requested count is small: if the count is "
        "smaller than the number of core workflows, emit the core workflows and stop. Only after "
        "every core workflow is covered may you add secondary tests (edge cases, UI/visual "
        "verification, minor variations) to fill the remainder up to the count. "
        "Example — a login feature's core workflows, IN THIS ORDER: "
        "(1) enter valid credentials → click Sign in → verify the post-login state is reached; "
        "(2) enter an invalid password → verify the error message is shown and the user stays on login; "
        "(3) submit with empty required fields → verify validation messages appear. "
        "Cosmetic checks (button color, logo, fonts, exact px) are SECONDARY — never index 0. "
        "Do NOT emit test cases in random order.",
```

- [ ] **Step 3c: Strengthen the count/coverage rule**

Replace this exact list item in `build_generation_user_message`:

```python
        "- Cover happy paths, edge cases, and negative scenarios where relevant.",
```
with:

```python
        "- Coverage order: the core functional workflows (happy + main negative/failure paths) "
        "come first and count toward the requested total; then edge cases and UI/visual checks "
        "fill the remainder. Do NOT pad with low-value cosmetic tests just to reach the count — "
        "prefer additional negative/edge variations of the core flow over cosmetic checks.",
```

- [ ] **Step 3d: Mirror the framing in `build_iterate_user_message`**

Replace this exact list item in `build_iterate_user_message`'s `extra_rules`:

```python
        "- CRITICAL PATH FIRST. Order the NEW test cases you generate by criticality — index "
        "0 of the array must be the most-critical NEW test that fulfills the instruction. "
        "Apply the same QA-lead 'must pass before release' lens within the scope of the "
        "instruction. Do NOT emit test cases in random order.",
```
with:

```python
        "- CORE / CRITICAL FIRST. Order the NEW test cases by criticality — index 0 must be the "
        "most-critical NEW test that fulfills the instruction, applying the QA-lead "
        "'must pass before release' lens. If the instruction implies core functional workflows "
        "not yet covered by context_test_cases, generate those before cosmetic/edge variations. "
        "Do NOT emit test cases in random order.",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/services/test_generation_priority_prompt.py -v`
Expected: PASS (4 tests). Then `python -m pytest -q` — full suite green (prompt-string change; other tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add backend/prompts/templates.py tests/services/test_generation_priority_prompt.py
git commit -m "feat: prioritize core functional workflows in generation prompt"
```

---

### Task 2: Docs

**Files:**
- Modify: `docs/limitations.md` (Test-case generation section)

- [ ] **Step 1: Update `docs/limitations.md`**

In the "Test-case generation" section, add a bullet noting the prompt now front-loads core functional workflows (happy + main negative paths) before cosmetic/edge tests, so critical flows aren't crowded out by visual-detail variations (especially for screenshots). Keep existing bullets.

- [ ] **Step 2: Commit**

```bash
git add docs/limitations.md
git commit -m "docs: note core-workflow-first generation"
```

---

## Self-review notes
- **Spec coverage:** core-first guarantee (T1 3b) · visual rebalance (T1 3a) · count interaction (T1 3c) · iterate mirror (T1 3d) · preferred_test_types filter unchanged (already core-first) · tests deterministic (T1 Step 1) · manual smoke is author-run (controller) · docs (T2). All spec sections map.
- **No new symbols;** signatures unchanged. Test asserts stable substrings the new strings contain (`core functional workflow`, `cosmetic`, `secondary`, `valid`/`invalid`, `fill`/`remainder`).
- Iterate builder signature confirmed: `build_iterate_user_message(existing, instruction, feature_filter, type_filter, ...)` — the test calls it positionally as `([], "add more login tests", None, None)`.
