"""Author user-message builder for ledger-grounded test case generation.

The Author runs as a regular JSON-output LLM call (reuses ``_complete_json``
from ``llm_service``). It is forbidden from inventing behavior because it
has no browser access and is instructed to cite evidence IDs from the
Evidence Ledger for every test case.

The Critic in ``browser_explorer/critic.py`` then validates the citations
and drops cases that don't resolve.
"""

from __future__ import annotations

import json

from backend.services.browser_explorer.ledger import ExplorationLedger


# Re-export the existing system prompt unchanged — the Verify-format and
# anti-vague-words rules from the main system prompt all still apply. We
# layer additional ledger-citation rules in the user message.
def build_author_user_message(
    *,
    ledger: ExplorationLedger,
    project_context: dict | None = None,
    target_feature_name: str = "",
    existing_titles: list[str] | None = None,
) -> str:
    """Build the user message for the Author LLM call.

    Embeds the full Evidence Ledger as JSON, lists known element names so
    the Author can quote them verbatim, and enforces the citation contract.
    """
    led = ledger.to_dict()
    # Trim any ledger field that's likely to blow up the prompt — screenshots
    # are referenced by ID only; we drop the ``file_path`` to keep the model
    # from quoting a server path.
    for s in led.get("screenshots", []):
        s.pop("file_path", None)

    known_names = sorted(ledger.known_element_names())
    rules = [
        "- Title format (STRICT): every title MUST start with \"Verify\" and reference an exact "
        "element name, page title, or error text from the ledger. Banned vague words in titles: "
        "\"correctly\", \"properly\", \"as expected\", \"works\", \"functions\".",
        "- CITATION REQUIREMENT (STRICT): every test case MUST include `evidence_refs: [\"id1\", "
        "\"id2\", ...]` listing every ledger ID it relies on (page IDs `pN`, action IDs `aN`, "
        "error IDs `eN`, form IDs `fN`, screenshot IDs `sN`). Inline citations like \"Click 'Sign "
        "Up' button [a3]\" are encouraged in step text. Cases with no `evidence_refs` will be "
        "rejected automatically.",
        "- DO NOT invent element names, button labels, error messages, URLs, or behaviors that "
        "don't appear in the ledger. If you can't find evidence in the ledger for a behavior, "
        "do not write a test for it.",
        "- For each form in `forms[]`, write at least one happy-path case (citing the happy-path "
        "submit action) and one negative case per observed error in `errors_observed[]` (citing "
        "the error.id and the action that triggered it).",
        "- Steps must reference the exact element names from the ledger, in quotes. Example: "
        "\"Click 'Sign Up' button [a3]\".",
        "- Selector hints in steps should prefer accessibility role+name over CSS. Example: "
        "\"Click button with name 'Sign Up'\" not \"Click button.btn-primary\".",
        "- Output ONLY valid JSON with key 'test_cases' (array). Each item: title, feature, type, "
        "preconditions, steps (array of strings), expected_result, priority (high|medium|low), "
        "evidence_refs (array of strings).",
        "- Cover happy paths AND negative scenarios grounded in observed errors. If "
        "errors_observed is non-empty, at least one negative test should cite each error.",
        "- Types allowed: happy, edge, negative, smoke, regression, integration, api, security, "
        "accessibility, performance, boundary, usability.",
        "- Do NOT duplicate any title from `existing_titles_in_project`.",
    ]

    body = {
        "goal": ledger.goal,
        "starting_url": ledger.starting_url,
        "project_context": project_context or {},
        "target_feature": target_feature_name,
        "evidence_ledger": led,
        "known_element_names": known_names[:200],
        "existing_titles_in_project": (existing_titles or [])[:200],
    }

    return (
        "Generate manual test cases grounded in the EVIDENCE LEDGER below. "
        "The ledger is the only source of truth — every test case must cite "
        "specific ledger IDs.\n\n"
        + json.dumps(body, ensure_ascii=False, indent=2)
        + "\n\n"
        "Rules:\n"
        + "\n".join(rules)
        + "\n\n"
        "Example response shape:\n"
        '{\n'
        '  "test_cases": [\n'
        '    {\n'
        '      "title": "Verify \'Email\' field rejects invalid format on Sign Up form submit",\n'
        '      "feature": "Signup",\n'
        '      "type": "negative",\n'
        '      "preconditions": "User on /signup with empty form [p2]",\n'
        '      "steps": [\n'
        '        "Type \\"not-an-email\\" into \'Email\' field [a4]",\n'
        '        "Click \'Sign Up\' button [a5]"\n'
        '      ],\n'
        '      "expected_result": "Error \\"Enter a valid email\\" appears under Email field [e1]",\n'
        '      "priority": "high",\n'
        '      "evidence_refs": ["p2", "a4", "a5", "e1"]\n'
        '    }\n'
        '  ]\n'
        '}\n'
    )
