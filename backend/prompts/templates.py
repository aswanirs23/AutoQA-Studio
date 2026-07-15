"""Prompt templates for test case generation.

- ``build_generation_user_message``: ``ParsedInput`` + project context + existing cases for target feature.
- ``build_iterate_user_message``: follow-up instructions; optional filter by feature/type; project context.

The system prompt (in ``llm_service``) and the JSON schema hint below steer models toward a single
JSON object with a ``test_cases`` array.
"""

import json
from typing import Any

from backend.models.test_case import TestCase
from backend.services.parsers.base import ParsedInput

_JSON_SCHEMA_HINT = """
Example response shape (strict JSON only):
{
  "test_cases": [
    {
      "title": "string",
      "feature": "string (match target feature when applicable)",
      "type": "happy|edge|negative|smoke|regression|integration|api|security|accessibility|performance|boundary|usability",
      "preconditions": "string (accounts, data, environment)",
      "steps": ["string", "..."],
      "expected_result": "string",
      "priority": "high|medium|low"
    }
  ]
}
"""

_TYPES_ALLOWED_RULE = (
    "- Types allowed: happy, edge, negative, smoke, regression, integration, api, security, accessibility, "
    "performance, boundary, usability."
)


def _build_project_context_block(project_description: str) -> str:
    """Render the PROJECT CONTEXT block. Uses a sentinel when the description is empty so
    the structure (and the rule line that references the block) is always present."""
    body = (project_description or "").strip() or "(no project description provided)"
    return (
        "=== PROJECT CONTEXT (apply to every test case) ===\n"
        + body
        + "\n=== END PROJECT CONTEXT ===\n"
    )


def _safe_metadata(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Drop sidechannel keys (underscore-prefixed) before serializing metadata to the LLM."""
    if not meta:
        return {}
    return {k: v for k, v in meta.items() if not str(k).startswith("_")}


def _serialize_existing_cases(cases: list[TestCase]) -> list[dict[str, Any]]:
    """Strip a list of TestCase models to the fields the LLM sees in prompts."""
    return [
        {
            "id": tc.id,
            "title": tc.title,
            "feature": tc.feature,
            "type": tc.type,
            "preconditions": tc.preconditions,
            "priority": tc.priority,
            "steps": tc.steps,
            "expected_result": tc.expected_result,
        }
        for tc in cases
    ]


_SOURCE_GUIDANCE = {
    "figma": (
        "The source is a Figma design. The DESIGN CONTEXT contains exact specs (sizes in px, "
        "hex colors, font family / size / weight, auto-layout padding, corner radius, component "
        "variants such as state=hover|disabled, prototype interactions) plus visible text. "
        "Developers build close to Figma — element names, button labels, and field names you "
        "see in the design are the same strings that end up in the built UI; use them verbatim "
        "in step text (click `Sign up`, not `register`). Derive end-to-end user flows from "
        "screen names, form fields, prototype interactions (ON_CLICK → NAVIGATE links), "
        "component variants, and the feature implied by the design. Cover form submission "
        "with valid and invalid data, validation rules implied by field types / placeholders / "
        "error copy, navigation flows between linked screens, role- or permission-based "
        "behavior implied by any user-role UI, empty / loading / error / disabled states "
        "implied by variants, multi-step workflows that span screens, and negative paths "
        "(missing fields, wrong data, unauthorized access, duplicate submissions, network "
        "failure). Every interactive element (button, input, dropdown, toggle, checkbox, "
        "link, tab) should have at least one functional test that exercises its purpose, not "
        "just its appearance. Do NOT make every test a 'displays correctly' / 'is visible' / "
        "'is styled' check — those alone are insufficient. Cover every variant axis you see."
    ),
    "screenshot": (
        "The source is a screenshot of the actual built app. DESIGN CONTEXT contains a "
        "vision-model description of the image. Every button text, field label, placeholder, "
        "and copy text visible in the screenshot is the exact string the user reads and that "
        "automation tools (Playwright) must match. In steps, reference these strings verbatim "
        "(click `Continue`, not `proceed`; enter into `Email address` field, not `email "
        "input`). Treat every visible interactive element as a real testable target."
    ),
    "jira": (
        "The source is a Jira issue. DESIGN CONTEXT contains the issue summary, description, "
        "acceptance criteria, and linked issues. Tests must verify each acceptance criterion."
    ),
    "browser_session": (
        "The source is a recorded browser session. DESIGN CONTEXT contains the URL, recorded "
        "steps, and snapshots. Tests must mirror the recorded flow and add edge cases for it."
    ),
    "text": (
        "The source is free-form requirements text. DESIGN CONTEXT contains the user-supplied "
        "content. Reference the specific behaviors, constraints, and rules it states."
    ),
}


def build_generation_user_message(
    parsed: ParsedInput,
    existing: list[TestCase],
    project_description: str = "",
    target_feature_name: str = "",
    extra_instruction: str | None = None,
    min_test_cases: int | None = None,
    preferred_test_types: list[str] | None = None,
) -> str:
    existing_payload = _serialize_existing_cases(existing)
    structured = {
        "source_type": parsed.source_type,
        "feature_name": parsed.feature_name,
        "screens": parsed.screens,
        "ui_elements": parsed.ui_elements,
        "user_actions": parsed.user_actions,
        "business_rules": parsed.business_rules,
        "metadata": _safe_metadata(parsed.metadata),
    }
    body: dict[str, Any] = {
        "target_feature": target_feature_name,
        "parsed_input_structured": structured,
        "existing_test_cases_for_target_feature": existing_payload,
    }
    if extra_instruction:
        body["extra_instruction"] = extra_instruction
    if min_test_cases is not None and min_test_cases > 0:
        body["min_new_test_cases"] = min_test_cases
    if preferred_test_types:
        body["preferred_test_types"] = preferred_test_types

    rules = [
        "- Ground every test case in the DESIGN CONTEXT below. Each test MUST reference specific "
        "element names, screen names, button labels, field labels, copy text, exact px sizes, "
        "hex colors, component variants, OR prototype interactions taken from that section. "
        "Generic test cases that could apply to any app are NOT acceptable.",
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
        "- Apply the PROJECT CONTEXT block above as constraint context for every test case. "
        "Any domain rules, business rules, compliance or regulatory hints in it must be "
        "reflected in preconditions, steps, or expected results when relevant.",
        "- TARGET FEATURE focus — primary then secondary. The TARGET FEATURE above is the "
        "PRIMARY scope. Generate tests for the TARGET FEATURE first and exhaust its reasonable "
        "coverage before adding tests for adjacent UI. When the DESIGN CONTEXT (especially "
        "Figma or screenshots) shows multiple features, prefer TARGET-FEATURE-related elements "
        "as the primary subject of tests; SECONDARY scope is the surrounding UI on the same "
        "screen, which you may test only if the requested count exceeds what the TARGET FEATURE "
        "can justify. Assign each test case's `feature` field to the TARGET FEATURE name. Even "
        "in secondary scope, tests must still ground in real elements from the DESIGN "
        "CONTEXT — no fabrication.",
        "- Output ONLY valid JSON with key 'test_cases' (array).",
        "- Each item: title, feature, type, preconditions (setup/data/environment), steps (array of strings), "
        "expected_result, priority (high|medium|low).",
        "- Title format (STRICT): every title MUST start with the word \"Verify\" and follow the "
        "shape: \"Verify <subject> <concrete behavior> [under <condition>]\". The subject must be "
        "an exact element / label / screen name from DESIGN CONTEXT. The behavior must be specific "
        "and observable. Do NOT use vague qualifiers — banned words in titles: \"correctly\", "
        "\"properly\", \"as expected\", \"works\", \"functions\" (as a verb meaning \"works\"). "
        "Examples:\n"
        "    GOOD: Verify 'Manage Team' header is displayed on User Management screen\n"
        "    GOOD: Verify Switch button toggles between active and inactive states on click\n"
        "    GOOD: Verify Continue button is disabled while Email field is empty\n"
        "    GOOD: Verify error message in #D32F2F appears under Email field on invalid format submit\n"
        "    GOOD: Verify Role dropdown opens and lists Admin, Editor, Viewer options on click\n"
        "    BAD:  Manage team header displays correctly  (no \"Verify\"; uses \"correctly\")\n"
        "    BAD:  Switch button toggles correctly  (vague — what does \"correctly\" mean?)\n"
        "    BAD:  Add new user button functions correctly  (vague behavior; banned word)\n"
        "    BAD:  Verify button works  (no specific element or behavior)",
        "- Steps must reference exact labels and field names from DESIGN CONTEXT, not paraphrased.",
        "- Expected results must cite the specific visual / spec value when relevant (e.g. \"Error text "
        "appears in #D32F2F under the Email field\").",
        "- Preconditions should state required data, roles, and environment so tests are executable.",
        "- Do NOT duplicate any existing test case (compare title and steps).",
        "- Coverage order: the core functional workflows (happy + main negative/failure paths) "
        "come first and count toward the requested total; then edge cases and UI/visual checks "
        "fill the remainder. Do NOT pad with low-value cosmetic tests just to reach the count — "
        "prefer additional negative/edge variations of the core flow over cosmetic checks.",
        _TYPES_ALLOWED_RULE,
    ]
    src_hint = _SOURCE_GUIDANCE.get(parsed.source_type)
    if src_hint:
        rules.insert(1, f"- {src_hint}")
    if min_test_cases is not None and min_test_cases > 0:
        rules.append(f"- Generate at least {min_test_cases} new test case(s) unless the input is too narrow.")
    if preferred_test_types:
        rules.append(
            "- Generate ONLY test cases of these types: " + ", ".join(preferred_test_types) + ". "
            "Within that filter, still order by criticality — the first emitted test must be "
            "the most-critical test of those types for this feature. Example: when filter = "
            "['smoke'], the first smoke test should verify the most-critical UI element for the "
            "feature (e.g., for a login feature, the 'Sign in' button is present and enabled — "
            "not the brand logo). When filter = ['edge'], the first edge test should be the most "
            "likely real-world edge for the feature."
        )

    raw = (parsed.raw_context or "").strip()
    design_block = (
        "=== DESIGN CONTEXT (primary source — ground every test case in these specifics) ===\n"
        + (raw if raw else "(no design context provided)")
        + "\n=== END DESIGN CONTEXT ===\n"
    )
    project_block = _build_project_context_block(project_description)

    return (
        "Generate NEW manual test cases as JSON.\n\n"
        + project_block
        + "\n"
        + design_block
        + "\n"
        + json.dumps(body, ensure_ascii=False, indent=2)
        + "\n\n"
        "Rules:\n"
        + "\n".join(rules)
        + "\n"
        + _JSON_SCHEMA_HINT
    )


SYSTEM_PROMPT = (
    "You are a senior QA engineer. You write clear, executable manual test cases that are "
    "tightly grounded in the supplied DESIGN CONTEXT — referencing specific element names, "
    "screen names, labels, copy text, exact px sizes, hex colors, fonts, and component "
    "variants present in the source material. You never produce generic test cases that "
    "could apply to any app. Visual precision (element names, px sizes, hex colors, fonts, "
    "component variants) governs HOW you write each test — NOT what you prioritize: a "
    "feature's core functional workflows come first, and purely cosmetic/visual-only checks "
    "are secondary and must never displace them. Every test case title begins with the word \"Verify\" and "
    "names a specific element and a concrete observable behavior — never vague words like "
    "\"correctly\", \"properly\", \"works\", or \"functions correctly\". You apply "
    "project-level rules, roles, and constraints from the JSON. You respond with strict "
    "JSON only."
)

OVERVIEW_SYSTEM_PROMPT = (
    "You are a senior QA/product analyst. You read product documentation and produce concise, "
    "structured project overviews for a test case generation tool. Respond with strict JSON only."
)


def build_overview_generation_prompt(extracted_text: str) -> str:
    """Prompt the LLM to create a project overview from uploaded document text."""
    return (
        "Read the following document text and produce a concise project overview that can be used "
        "as context for AI-based test case generation. The overview should capture:\n"
        "- Product/application name and purpose\n"
        "- Key features and modules\n"
        "- User roles and personas\n"
        "- Business rules and constraints\n"
        "- Technology stack (if mentioned)\n"
        "- Important workflows\n\n"
        "Respond with a JSON object: {\"overview\": \"<the overview text>\"}\n\n"
        "--- DOCUMENT TEXT ---\n"
        + extracted_text[:30000]
    )


def build_iterate_user_message(
    existing: list[TestCase],
    instruction: str,
    feature_filter: str | None,
    type_filter: str | None,
    project_description: str = "",
    min_test_cases: int | None = None,
    preferred_test_types: list[str] | None = None,
) -> str:
    filtered = existing
    if feature_filter:
        ff = feature_filter.strip().lower()
        filtered = [tc for tc in filtered if ff in (tc.feature or "").lower()]
    if type_filter:
        filtered = [tc for tc in filtered if tc.type == type_filter]

    existing_payload = _serialize_existing_cases(filtered)
    body: dict[str, Any] = {
        "instruction": instruction,
        "context_test_cases": existing_payload,
    }
    if min_test_cases is not None and min_test_cases > 0:
        body["min_new_test_cases"] = min_test_cases
    if preferred_test_types:
        body["preferred_test_types"] = preferred_test_types

    extra_rules = [
        "- Apply the PROJECT CONTEXT block above as constraint context for every test case. "
        "Any domain rules, business rules, compliance or regulatory hints in it must be "
        "reflected in preconditions, steps, or expected results when relevant.",
        "- CORE / CRITICAL FIRST. Order the NEW test cases by criticality — index 0 must be the "
        "most-critical NEW test that fulfills the instruction, applying the QA-lead "
        "'must pass before release' lens. If the instruction implies core functional workflows "
        "not yet covered by context_test_cases, generate those before cosmetic/edge variations. "
        "Do NOT emit test cases in random order.",
        "- The context_test_cases below already exist for this feature. Your output MUST NOT "
        "duplicate any of them. Do not repeat a title, do not repeat the same sequence of "
        "steps with cosmetic changes, and do not re-test the same exact behavior under a "
        "slightly different name. Each new test must cover a behavior, edge, negative "
        "scenario, or UI check that is NOT already covered by context_test_cases.",
        "- Output ONLY valid JSON with key 'test_cases' (array).",
        "- Each item: title, feature, type, preconditions, priority, steps, expected_result.",
        "- Title format (STRICT): every title MUST start with \"Verify\" and follow "
        "\"Verify <subject> <concrete behavior> [under <condition>]\" — subject is an exact "
        "element / label / screen name; behavior is specific and observable. Banned vague "
        "words in titles: \"correctly\", \"properly\", \"as expected\", \"works\", \"functions\". "
        "Example GOOD: \"Verify Continue button is disabled while Email field is empty\". "
        "Example BAD: \"Continue button works correctly\".",
        _TYPES_ALLOWED_RULE,
    ]
    if min_test_cases is not None and min_test_cases > 0:
        extra_rules.append(f"- Generate at least {min_test_cases} new test case(s) where possible.")
    if preferred_test_types:
        extra_rules.append(
            "- Generate ONLY test cases of these types: " + ", ".join(preferred_test_types) + ". "
            "Within that filter, still order by criticality."
        )

    project_block = _build_project_context_block(project_description)

    return (
        "Based on the project context and existing tests below, generate ADDITIONAL new test cases.\n\n"
        + project_block
        + "\n"
        + json.dumps(body, ensure_ascii=False, indent=2)
        + "\n\n"
        "Rules:\n"
        + "\n".join(extra_rules)
        + "\n"
        + _JSON_SCHEMA_HINT
    )


PLAYWRIGHT_SYSTEM_PROMPT = (
    "You are a senior QA automation engineer. You translate a single manual test case "
    "into a runnable Playwright Python async test. You output ONLY Python source code "
    "for one async function with this exact signature:\n"
    "    async def test(page, base_url):\n"
    "  or, for login-flow tests, exactly:\n"
    "    async def test(page, base_url, username, password):\n"
    "No markdown fences, no commentary, no imports — just the function definition with the "
    "signature the request specifies. "
    "Inside the function:\n"
    "- Start with `await page.goto(base_url + '<path>')`. Infer the path from the test "
    "case title, preconditions, or steps. Common conventions: '/login' or '/signin' for "
    "auth flows, '/dashboard' or '/' for post-login views, '/checkout' for purchase flows. "
    "Use '/' only if no path is implied.\n"
    "- IMMEDIATELY after `page.goto`, wait for the page to actually render content. "
    "Modern SPAs serve an empty shell first. Add `await page.wait_for_load_state('networkidle', timeout=8000)` "
    "right after goto, inside a try/except that catches `Exception` (some pages never reach networkidle).\n"
    "- For inputs (Username, Email, Password, etc.), prefer in this order: "
    "`page.get_by_placeholder('X')`, `page.get_by_role('textbox', name='X')`, "
    "`page.locator('input[name=\"x\"]')`. AVOID `page.get_by_label('X')` for "
    "inputs — many real forms use placeholders or aria-labels instead of "
    "actual <label> elements, and get_by_label only matches a proper <label> "
    "association.\n"
    "- For buttons / links / headings, use `page.get_by_role('button', name='X')`, "
    "`page.get_by_role('link', name='X')`, `page.get_by_role('heading', name='X')`. "
    "For body text, `page.get_by_text('X')`. Avoid raw CSS unless necessary.\n"
    "- For visibility checks, use `await page.locator(...).first.wait_for(state='visible', timeout=8000)` "
    "BEFORE asserting — this gives elements time to render and produces a clearer "
    "TimeoutError on failure than a bare `assert .is_visible()`.\n"
    "- End with one or more `assert` statements verifying the expected result. Include "
    "a descriptive message: `assert condition, 'why this should be true'`.\n"
    "- Do not use `subprocess`, `os.system`, `eval`, `exec`, `__import__`, `open(`, "
    "`requests.`, `urllib.`, `socket.`, `shutil.`, or `pathlib.Path`. The runtime will "
    "reject any code containing those tokens. "
    "If the user message includes a LIVE PAGE SNAPSHOT, treat it as ground truth for "
    "the landing page: use only roles/names/text that appear in it for the first "
    "screen, and reach later screens by interacting with elements that are present "
    "rather than guessing selectors or raw CSS classes."
)


def build_playwright_user_message(tc: dict, base_url: str, *, is_login: bool = False,
                                  landing_path: str = "", has_credentials: bool = False,
                                  page_snapshot: str = "") -> str:
    """Build the user-side prompt for Playwright code generation from a test case dict."""
    steps_section = "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(tc.get('steps') or []))
    header = (
        "Generate a Playwright Python async test for the following manual test case.\n\n"
        f"BASE_URL: {base_url}\n\n"
    )
    body = (
        f"Title: {tc.get('title', '')}\n"
        f"Preconditions: {tc.get('preconditions', '')}\n"
        f"Steps:\n{steps_section}\n"
        f"Expected result: {tc.get('expected_result', '')}\n\n"
    )
    if page_snapshot.strip():
        body += (
            "LIVE PAGE SNAPSHOT (accessibility tree of the landing page under test).\n"
            "These are the REAL elements present on the first screen. Bind your "
            "selectors to roles/names that appear here. If a step needs an element "
            "that is NOT in this snapshot (e.g. a later screen), navigate there via "
            "the on-screen controls that ARE here first — do not invent selectors:\n"
            f"{page_snapshot.strip()}\n\n"
        )
    if is_login and has_credentials:
        directive = (
            "This is a LOGIN test. Use EXACTLY this signature:\n"
            "    async def test(page, base_url, username, password):\n"
            "Navigate to the login page with `await page.goto(base_url + '/')` (or the login path "
            "implied by the steps). Fill the username/email field with the `username` parameter and "
            "the password field with the `password` parameter — NEVER hard-code credential values. "
            "Submit, then assert the post-login state.\n"
            "Output ONLY the `async def test(page, base_url, username, password):` function and its body."
        )
    else:
        target = f"base_url + '{landing_path}'" if landing_path else "base_url + '/'"
        directive = (
            "Use EXACTLY this signature:\n"
            "    async def test(page, base_url):\n"
            f"Start with `await page.goto({target})` — this is the page under test. "
            "Then perform the steps and assert the expected result.\n"
            "Output ONLY the `async def test(page, base_url):` function and its body."
        )
    return header + body + directive


EXPECTED_RESULT_REWRITE_SYSTEM_PROMPT = (
    "You are a senior QA engineer rewriting a single test case's expected result "
    "so it accurately describes the observed behavior of an app under test. You "
    "receive the original expected_result (what the test was looking for), the "
    "actual page text the test saw (what was actually rendered), and optionally "
    "the failed assertion's error message.\n\n"
    "Your job: produce a single paragraph (one to three sentences) that documents "
    "the observed behavior in the same documentary tone as the original. Generalize "
    "where appropriate (use \"an error message indicating ...\" rather than copying "
    "the exact app string verbatim), but stay grounded in what the page actually "
    "showed. Do not invent behavior the page text doesn't support.\n\n"
    "Output ONLY the rewritten expected_result text. No markdown, no commentary, "
    "no quoting."
)


def build_expected_result_rewrite_user_message(
    current_expected_result: str,
    actual_page_text: str,
    error_message: str = "",
) -> str:
    """Build the user-side prompt for the expected_result rewrite."""
    err = error_message.strip() if error_message else "(none provided)"
    return (
        "Original expected_result:\n"
        f"{current_expected_result}\n\n"
        "Actual page text observed:\n"
        f"{actual_page_text}\n\n"
        "Failed assertion error:\n"
        f"{err}\n\n"
        "Rewrite the expected_result to describe the observed behavior accurately."
    )


HEAL_SYSTEM_PROMPT = (
    "You are a senior QA automation engineer performing SELF-HEALING of a failed "
    "Playwright test. The test failed, but the user has confirmed the page's ACTUAL "
    "observed behavior is CORRECT. Treat the provided accessibility snapshot of the "
    "observed page as the source of truth.\n"
    "Do two things:\n"
    "1. Rewrite the test case's expected_result to accurately describe the observed "
    "behavior.\n"
    "2. Fix the Playwright test code so its selectors and assertions match the observed "
    "snapshot and PASS against that page. Preserve the code's navigation/step intent and "
    "its EXACT function signature (keep `async def test(page, base_url)` or "
    "`async def test(page, base_url, username, password)` as given). Do not hard-code "
    "credentials.\n"
    "Respond with ONLY a JSON object of the form "
    '{"suggested_expected": "<text>", "suggested_code": "<python source>"}. '
    "No markdown, no commentary."
)


def build_heal_prompt(tc: dict, current_code: str, page_snapshot: str, error_message: str) -> str:
    steps_section = "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(tc.get('steps') or []))
    return (
        "Heal this failed test using the observed page as the correct behavior.\n\n"
        f"Title: {tc.get('title', '')}\n"
        f"Steps:\n{steps_section}\n"
        f"Current expected_result: {tc.get('expected_result', '')}\n\n"
        f"Failure message:\n{error_message}\n\n"
        f"Observed page (accessibility snapshot — this is correct):\n{page_snapshot}\n\n"
        f"Current Playwright code:\n{current_code}\n\n"
        "Return JSON with keys suggested_expected and suggested_code."
    )
