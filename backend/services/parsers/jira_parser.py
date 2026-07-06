"""Jira issue parser via Jira Cloud REST API.

Steps:
1. Read JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN from settings.
2. GET /rest/api/3/issue/{key} with Basic auth (email:api_token).
3. Extract summary, description (ADF → plain text), status, labels.
4. Optional: fetch subtasks and linked issues when include_linked=true in data.
5. Heuristic lines into business_rules; full text in raw_context.
"""

import base64
from typing import Any

import httpx
from starlette.datastructures import UploadFile

from backend.config import get_effective_settings
from backend.services.parsers.base import BaseParser, InputFieldDef, ParsedInput, ParserMeta
from backend.services.parsers.registry import ParserRegistry


def _extract_text_from_adf(adf: dict[str, Any] | None) -> str:
    """Plain text from Atlassian Document Format (paragraphs, lists, tables, code)."""
    if not adf:
        return ""
    parts: list[str] = []

    def walk(node: dict[str, Any], depth: int = 0) -> None:
        if depth > 400:
            return
        ntype = node.get("type")
        if ntype == "text":
            parts.append(node.get("text", ""))
        elif ntype == "hardBreak":
            parts.append("\n")
        elif ntype == "paragraph":
            for c in node.get("content") or []:
                walk(c, depth + 1)
            parts.append("\n")
        elif ntype in ("bulletList", "orderedList"):
            for c in node.get("content") or []:
                walk(c, depth + 1)
        elif ntype == "listItem":
            parts.append("- ")
            for c in node.get("content") or []:
                walk(c, depth + 1)
            parts.append("\n")
        elif ntype == "heading":
            level = node.get("attrs") or {}
            lv = level.get("level", 1)
            parts.append("#" * min(int(lv), 6) + " ")
            for c in node.get("content") or []:
                walk(c, depth + 1)
            parts.append("\n")
        elif ntype == "codeBlock":
            for c in node.get("content") or []:
                walk(c, depth + 1)
            parts.append("\n")
        elif ntype == "blockquote":
            for c in node.get("content") or []:
                walk(c, depth + 1)
            parts.append("\n")
        elif ntype == "table":
            for row in node.get("content") or []:
                if row.get("type") == "tableRow":
                    cells: list[str] = []
                    for cell in row.get("content") or []:
                        cell_parts: list[str] = []

                        def cell_walk(n: dict[str, Any]) -> None:
                            if n.get("type") == "text":
                                cell_parts.append(n.get("text", ""))
                            for ch in n.get("content") or []:
                                cell_walk(ch)

                        cell_walk(cell)
                        cells.append(" ".join(cell_parts).strip())
                    if cells:
                        parts.append(" | ".join(cells) + "\n")
        else:
            for c in node.get("content") or []:
                walk(c, depth + 1)

    walk(adf)
    text = "".join(parts)
    return text.strip()


class JiraParser(BaseParser):
    meta = ParserMeta(
        name="jira",
        display_name="Jira issue",
        description="Fetch a Jira story, bug, or epic by key (REST API). Optional linked issues.",
        input_fields=[
            InputFieldDef(
                name="issue_key",
                type="text",
                label="Issue key",
                placeholder="e.g. PROJ-123",
                required=True,
            ),
        ],
        accepts_file=False,
    )

    async def parse(self, data: dict[str, Any], file: UploadFile | None) -> ParsedInput:
        settings = get_effective_settings()
        base = (settings.jira_base_url or "").rstrip("/")
        email = settings.jira_email
        token = settings.jira_api_token
        if not base or not email or not token:
            raise ValueError("JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN must be set (.env or API keys in the UI)")

        key = str(data.get("issue_key") or data.get("key") or "").strip().upper()
        if not key:
            raise ValueError("issue_key is required")

        include_linked = str(data.get("include_linked") or "").lower() in ("1", "true", "yes")

        auth = base64.b64encode(f"{email}:{token}".encode()).decode()
        headers = {
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
        }
        url = f"{base}/rest/api/3/issue/{key}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            issue = r.json()

        fields = issue.get("fields") or {}
        summary = fields.get("summary") or ""
        desc = fields.get("description")
        desc_text = _extract_text_from_adf(desc) if isinstance(desc, dict) else str(desc or "")
        issuetype = (fields.get("issuetype") or {}).get("name") or ""
        status = (fields.get("status") or {}).get("name") or ""
        labels = fields.get("labels") or []

        extra_blocks: list[str] = []
        subtasks = fields.get("subtasks") or []
        if isinstance(subtasks, list) and subtasks:
            extra_blocks.append("Subtasks:")
            for st in subtasks[:50]:
                sk = (st.get("key") or "") if isinstance(st, dict) else ""
                ss = ""
                if isinstance(st, dict):
                    fs = st.get("fields") or {}
                    ss = (fs.get("summary") or "") if isinstance(fs, dict) else ""
                if sk:
                    extra_blocks.append(f"  - {sk}: {ss}")

        links = fields.get("issuelinks") or []
        if include_linked and isinstance(links, list) and links:
            extra_blocks.append("Linked issues:")
            for ln in links[:50]:
                if not isinstance(ln, dict):
                    continue
                outward = ln.get("outwardIssue")
                inward = ln.get("inwardIssue")
                for tag, iss in (("outward", outward), ("inward", inward)):
                    if isinstance(iss, dict):
                        lk = iss.get("key") or ""
                        fs = iss.get("fields") or {}
                        summ = (fs.get("summary") or "") if isinstance(fs, dict) else ""
                        if lk:
                            extra_blocks.append(f"  - {lk}: {summ}")

        # Pull acceptance criteria from description if present
        ac_lines = [
            ln
            for ln in desc_text.splitlines()
            if "acceptance" in ln.lower() or "criteria" in ln.lower() or "given" in ln.lower()
        ]

        feature = str(data.get("feature_name") or summary or key).strip()

        raw_context = "\n".join(
            [
                f"Issue: {key}",
                f"Type: {issuetype}",
                f"Status: {status}",
                f"Summary: {summary}",
                f"Description:\n{desc_text}",
                f"Labels: {', '.join(labels) if labels else 'none'}",
            ]
        )
        if extra_blocks:
            raw_context += "\n\n" + "\n".join(extra_blocks)
        if ac_lines:
            raw_context += "\n\nRelated / AC lines:\n" + "\n".join(ac_lines[:40])

        return ParsedInput(
            source_type="jira",
            feature_name=feature or key,
            screens=[],
            ui_elements=[],
            user_actions=[],
            business_rules=[
                ln
                for ln in desc_text.splitlines()
                if any(k in ln.lower() for k in ("must", "should", "validation", "error", "required"))
            ][:40],
            raw_context=raw_context,
            metadata={
                "issue_key": key,
                "issue_type": issuetype,
                "status": status,
                "include_linked": include_linked,
            },
        )


# Self-register on import
ParserRegistry.register(JiraParser())
