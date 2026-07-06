"""Unified generation and iteration API (project + feature scoped, persistent).

Request flow (high level):
- POST /api/generate accepts either JSON or multipart form. It can run a single parser
  (input_type + data [+ file]) or multiple sources (inputs[] + optional files[]).
- Parsers turn raw input into ParsedInput; the LLM returns new TestCase rows.
- Dedup removes cases whose content hash already exists in the project; survivors are inserted.

POST /api/generate/iterate refines or adds cases from existing tests + a natural-language instruction.
"""

import json
import logging
import uuid as _uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile

from backend.config import get_effective_settings
from backend.db import get_db
from backend.deps import get_current_user_id
from backend.models.requests import GenerateIterateBody, GenerateResponse
from backend.models.test_case import TestCase
from backend.repositories import feature_repo, generation_repo, input_repo, project_repo, testcase_repo
from backend.services.dedup_service import filter_duplicates
from backend.services.llm_service import generate_from_parsed, generate_iterate
from backend.services.parsers.merge_parsed import merge_parsed_inputs
from backend.services.parsers.base import ParsedInput
from backend.services.parsers.registry import ParserRegistry
from backend.services.source_ref import derive_generation_summary, derive_source_ref
from backend.services.upstream_errors import map_upstream_exception

logger = logging.getLogger(__name__)

router = APIRouter(tags=["generate"])


def _normalize_feature_name(cases: list[TestCase], default_name: str) -> list[TestCase]:
    """Ensure each case uses the target feature name when the model leaves `feature` blank."""
    out: list[TestCase] = []
    for tc in cases:
        fn = (tc.feature or "").strip() or default_name
        out.append(tc.model_copy(update={"feature": fn}))
    return out


def _inject_llm(data: dict[str, Any], llm_provider: str | None, llm_model: str | None) -> dict[str, Any]:
    """Pass-through fields parsers read to align vision/LLM with the UI (_llm_provider / _llm_model)."""
    out = dict(data)
    if llm_provider:
        out = {**out, "_llm_provider": llm_provider.strip()}
    if llm_model:
        out = {**out, "_llm_model": llm_model.strip()}
    return out


def _data_root() -> Path:
    """Project data directory (sibling of backend/)."""
    return Path(__file__).resolve().parents[2] / "data"


def _persist_image(*, feature_id: str, generation_id: str, input_id: str,
                   image_bytes: bytes, ext: str) -> str:
    """Write image bytes under data/generations/<feature_id>/<generation_id>/<input_id>.<ext>;
    return the path relative to data/ for storage in the DB."""
    rel_dir = Path("generations") / feature_id / generation_id
    abs_dir = _data_root() / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)
    safe_ext = (ext or "png").lstrip(".").lower()[:5] or "png"
    fname = f"{input_id}.{safe_ext}"
    (abs_dir / fname).write_bytes(image_bytes)
    return str(rel_dir / fname)


def _build_generation_inputs_from_parsed(parsed: ParsedInput) -> list[dict[str, Any]]:
    """Map a ParsedInput (single or multi) to row dicts for generation_inputs.

    For each row, fields are: source_type, url, text_content, image_path, summary,
    plus optional `_image_bytes`/`_image_ext` sidechannels (popped by the caller).
    """
    def _row_for_single(src_type: str, meta: dict[str, Any], feature_name: str, raw_context: str) -> dict[str, Any]:
        row: dict[str, Any] = {"source_type": src_type, "summary": ""}
        if src_type == "jira":
            row["url"] = (meta.get("url") or "").strip() or None
            key = (meta.get("issue_key") or "").strip()
            title = (meta.get("title") or "").strip()
            row["summary"] = key or title or "Jira"
            return row
        if src_type == "figma":
            row["url"] = (meta.get("url") or "").strip() or None
            row["summary"] = (
                meta.get("file_name") or meta.get("frame_name") or "Figma"
            )
            row["summary"] = str(row["summary"]).strip()
            return row
        if src_type == "browser_session":
            row["url"] = (meta.get("url") or "").strip() or None
            row["summary"] = (meta.get("goal") or meta.get("session_id") or "Browser session")
            row["summary"] = str(row["summary"]).strip()
            return row
        if src_type == "text":
            row["text_content"] = raw_context or ""
            row["summary"] = (feature_name or "Manual text")[:160]
            return row
        if src_type == "screenshot":
            row["summary"] = (meta.get("filename") or "Screenshot")
            row["summary"] = str(row["summary"]).strip()
            row["_image_bytes"] = meta.get("_image_bytes")
            row["_image_ext"] = meta.get("_image_ext") or "png"
            return row
        row["summary"] = src_type
        return row

    if (parsed.source_type or "").lower() != "multi":
        return [_row_for_single(parsed.source_type, parsed.metadata or {}, parsed.feature_name, parsed.raw_context)]

    sources = (parsed.metadata or {}).get("sources") or []
    rows: list[dict[str, Any]] = []
    for s in sources:
        if not isinstance(s, dict):
            continue
        rows.append(_row_for_single(
            str(s.get("source_type") or "unknown"),
            dict(s.get("metadata") or {}),
            str(s.get("feature_name") or ""),
            str(s.get("raw_context") or ""),
        ))
    return rows


async def _persist_generation(
    *,
    db,
    project_id: str,
    feature_id: str,
    trigger: str,
    parsed: ParsedInput,
    source_ref_override: str | None = None,
    summary_override: str | None = None,
) -> None:
    """Write one generations row + N generation_inputs rows. Caller must have inserted >=1 test case."""
    rows = _build_generation_inputs_from_parsed(parsed)
    source_ref = source_ref_override if source_ref_override is not None else derive_source_ref(parsed)
    summary = summary_override if summary_override is not None else derive_generation_summary(parsed)

    gen_id = str(_uuid.uuid4())
    prepared: list[dict[str, Any]] = []
    image_writes: list[tuple[str, bytes, str]] = []
    for idx, r in enumerate(rows):
        input_id = str(_uuid.uuid4())
        image_bytes = r.pop("_image_bytes", None)
        image_ext = r.pop("_image_ext", "png")
        if image_bytes:
            image_writes.append((input_id, image_bytes, image_ext))
        prepared.append({
            "id_hint": input_id,
            "sort_order": idx,
            **r,
        })

    # Write image files first so we have paths to store on each row.
    for input_id, b, ext in image_writes:
        rel = _persist_image(
            feature_id=feature_id,
            generation_id=gen_id,
            input_id=input_id,
            image_bytes=b,
            ext=ext,
        )
        for p in prepared:
            if p["id_hint"] == input_id:
                p["image_path"] = rel
                break

    # Insert generations row with the pre-known gen_id (matches on-disk directory name).
    await db.execute(
        """
        INSERT INTO generations(id, project_id, feature_id, trigger, source_ref, summary, created_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (gen_id, project_id, feature_id, trigger, source_ref, summary),
    )
    for idx, row in enumerate(prepared):
        # id_hint is always the input row's PK; it matches the image filename when present.
        await db.execute(
            """
            INSERT INTO generation_inputs(
                id, generation_id, source_type, url, text_content, image_path, summary, sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id_hint"],
                gen_id,
                row.get("source_type") or "unknown",
                row.get("url"),
                row.get("text_content"),
                row.get("image_path"),
                row.get("summary") or "",
                int(row.get("sort_order", idx)),
            ),
        )


async def _parse_one(
    input_type: str,
    data: dict[str, Any],
    file: UploadFile | None,
    llm_provider: str | None,
    llm_model: str | None,
) -> ParsedInput:
    """Dispatch to ParserRegistry by name; map parser failures to HTTP 400."""
    data = _inject_llm(data, llm_provider, llm_model)
    try:
        parser = ParserRegistry.get(input_type)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unknown input_type: {input_type}") from None

    if parser.meta.accepts_file and file is None:
        raise HTTPException(status_code=400, detail=f"Input type {input_type} requires a file upload")

    try:
        return await parser.parse(data, file)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise map_upstream_exception("Parser error", e) from e


async def _execute_generation(
    parsed: ParsedInput,
    *,
    project_id: str,
    feature_id: str,
    proj: Any,
    feat: Any,
    existing_for_prompt: list[TestCase],
    hashes: set[str],
    llm_provider: str | None,
    llm_model: str | None,
    min_test_cases: int | None,
    preferred_test_types: list[str] | None,
    input_type_log: str,
    input_metadata_extra: dict[str, Any] | None = None,
) -> GenerateResponse:
    """Call LLM, dedupe against project hashes, persist new rows and input_history."""
    target_name = feat.name
    if parsed.feature_name and parsed.feature_name.strip():
        parsed = parsed.model_copy(update={"feature_name": target_name})

    project_description = proj.description or ""

    settings = get_effective_settings()
    try:
        generated = await generate_from_parsed(
            parsed,
            existing_for_prompt,
            settings,
            provider_override=llm_provider,
            model_override=llm_model,
            project_description=project_description,
            target_feature_name=target_name,
            min_test_cases=min_test_cases,
            preferred_test_types=preferred_test_types,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise map_upstream_exception("LLM error", e) from e

    generated = _normalize_feature_name(generated, target_name)
    source_ref = derive_source_ref(parsed)
    generated = [tc.model_copy(update={"source_ref": source_ref}) for tc in generated]

    # Critic pass for AI-explored browser sessions: drop test cases whose
    # inline `[xN]` ledger citations don't resolve. Source_ref is also
    # rewritten to the resolved evidence (concrete URLs / actions / errors).
    critic_dropped: list[dict] = []
    if (parsed.metadata or {}).get("mode") == "ai_explore" and (parsed.metadata or {}).get("evidence_ledger"):
        from backend.services.browser_explorer.critic import apply_to_test_cases
        from backend.services.browser_explorer.ledger import ExplorationLedger as _Ledger

        try:
            _ledger = _Ledger.from_dict(parsed.metadata["evidence_ledger"])
            generated, critic_dropped = apply_to_test_cases(generated, _ledger)
            if critic_dropped:
                logger.warning(
                    "Critic dropped %d/%d AI-explore cases (unresolved citations); first reason: %s",
                    len(critic_dropped),
                    len(critic_dropped) + len(generated),
                    critic_dropped[0].get("_drop_reason"),
                )
        except Exception:
            logger.exception("Critic pass failed; falling back to ungated cases")

    new_cases, skipped = filter_duplicates(generated, hashes)

    meta_log = dict(parsed.metadata) if parsed.metadata else {}
    if input_metadata_extra:
        meta_log = {**meta_log, **input_metadata_extra}

    if not new_cases:
        async with get_db() as db:
            await input_repo.add_input_record(
                db,
                project_id,
                input_type_log,
                f"No new cases after dedup (parsed: {parsed.source_type})",
                meta_log,
                feature_id=feature_id,
            )
        return GenerateResponse(
            added_count=0,
            skipped_duplicate_count=skipped,
            test_cases=[],
            parsed_summary=parsed.raw_context[:2000],
        )

    async with get_db() as db:
        added = await testcase_repo.insert_test_cases(db, project_id, feature_id, target_name, new_cases)
        if added:
            await _persist_generation(
                db=db,
                project_id=project_id,
                feature_id=feature_id,
                trigger="generate",
                parsed=parsed,
            )
        await input_repo.add_input_record(
            db,
            project_id,
            input_type_log,
            f"Added {len(added)} test case(s)",
            meta_log,
            feature_id=feature_id,
        )

    return GenerateResponse(
        added_count=len(added),
        skipped_duplicate_count=skipped,
        test_cases=added,
        parsed_summary=parsed.raw_context[:2000],
    )


async def run_generate_core(
    input_type: str,
    project_id: str,
    feature_id: str,
    data: dict[str, Any],
    file: UploadFile | None,
    llm_provider: str | None,
    llm_model: str | None,
    user_id: str,
    min_test_cases: int | None = None,
    preferred_test_types: list[str] | None = None,
) -> GenerateResponse:
    """Single parser path: one input_type, optional uploaded file."""
    async with get_db() as db:
        proj = await project_repo.get_project(db, user_id, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        feat = await feature_repo.get_feature(db, user_id, project_id, feature_id)
        if not feat:
            raise HTTPException(status_code=404, detail="Feature not found")
        existing_for_prompt = await testcase_repo.list_test_cases_for_feature(db, project_id, feature_id)
        hashes = await testcase_repo.existing_hashes_for_project(db, project_id)

    parsed = await _parse_one(input_type, data, file, llm_provider, llm_model)

    return await _execute_generation(
        parsed,
        project_id=project_id,
        feature_id=feature_id,
        proj=proj,
        feat=feat,
        existing_for_prompt=existing_for_prompt,
        hashes=hashes,
        llm_provider=llm_provider,
        llm_model=llm_model,
        min_test_cases=min_test_cases,
        preferred_test_types=preferred_test_types,
        input_type_log=input_type,
    )


async def run_generate_multi(
    items: list[dict[str, Any]],
    files: list[UploadFile],
    project_id: str,
    feature_id: str,
    user_id: str,
    llm_provider: str | None,
    llm_model: str | None,
    min_test_cases: int | None,
    preferred_test_types: list[str] | None,
) -> GenerateResponse:
    """Multi-source path: parse each item, merge into one ParsedInput, then same LLM + DB path."""
    if not items:
        raise HTTPException(status_code=400, detail="inputs must contain at least one item")
    if len(items) > 20:
        raise HTTPException(status_code=400, detail="At most 20 input sources per request")

    async with get_db() as db:
        proj = await project_repo.get_project(db, user_id, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        feat = await feature_repo.get_feature(db, user_id, project_id, feature_id)
        if not feat:
            raise HTTPException(status_code=404, detail="Feature not found")
        existing_for_prompt = await testcase_repo.list_test_cases_for_feature(db, project_id, feature_id)
        hashes = await testcase_repo.existing_hashes_for_project(db, project_id)

    parsed_list: list[ParsedInput] = []
    types_order: list[str] = []

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail=f"inputs[{i}] must be an object")
        itype = str(item.get("input_type") or "").strip()
        if not itype:
            raise HTTPException(status_code=400, detail=f"inputs[{i}].input_type is required")
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        file: UploadFile | None = None
        fi = item.get("file_index")
        if fi is not None:
            try:
                idx = int(fi)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"inputs[{i}].file_index must be an integer") from None
            if idx < 0 or idx >= len(files):
                raise HTTPException(
                    status_code=400,
                    detail=f"inputs[{i}].file_index {idx} out of range (0..{len(files) - 1})",
                )
            file = files[idx]
        p = await _parse_one(itype, data, file, llm_provider, llm_model)
        parsed_list.append(p)
        types_order.append(itype)

    merged = merge_parsed_inputs(parsed_list, feat.name)

    input_type_log = "multi:" + "+".join(types_order)
    extra = {"input_types": types_order, "count": len(items)}

    return await _execute_generation(
        merged,
        project_id=project_id,
        feature_id=feature_id,
        proj=proj,
        feat=feat,
        existing_for_prompt=existing_for_prompt,
        hashes=hashes,
        llm_provider=llm_provider,
        llm_model=llm_model,
        min_test_cases=min_test_cases,
        preferred_test_types=preferred_test_types,
        input_type_log=input_type_log[:120],
        input_metadata_extra=extra,
    )


def _parse_inputs_json(raw: Any) -> list[dict[str, Any]]:
    """Normalize `inputs` from form (string) or JSON body into a list of dicts."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON in inputs field") from None
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="inputs must be a JSON array")
    out: list[dict[str, Any]] = []
    for i, x in enumerate(raw):
        if not isinstance(x, dict):
            raise HTTPException(status_code=400, detail=f"inputs[{i}] must be an object")
        out.append(x)
    return out


@router.post("/generate", response_model=GenerateResponse)
async def generate(
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> GenerateResponse:
    """Single or multi generate; JSON body vs multipart (file uploads) handled separately."""
    content_type = request.headers.get("content-type", "")
    file: UploadFile | None = None
    files: list[UploadFile] = []
    llm_provider: str | None = None
    llm_model: str | None = None

    min_tc: int | None = None
    pref_types: list[str] | None = None

    if "application/json" in content_type:
        body = await request.json()
        project_id = str(body.get("project_id") or "")
        feature_id = str(body.get("feature_id") or "")
        llm_provider = body.get("llm_provider")
        lm = body.get("llm_model")
        llm_model = str(lm).strip() if lm is not None and str(lm).strip() else None
        mtc = body.get("min_test_cases")
        if mtc is not None:
            try:
                min_tc = max(1, int(mtc))
            except (TypeError, ValueError):
                min_tc = None
        pt = body.get("preferred_test_types")
        if isinstance(pt, list):
            pref_types = [str(x).strip().lower() for x in pt if str(x).strip()]
        elif isinstance(pt, str) and pt.strip():
            pref_types = [s.strip().lower() for s in pt.split(",") if s.strip()]

        inputs_raw = body.get("inputs")
        if inputs_raw is not None:
            items = _parse_inputs_json(inputs_raw)
            if not items:
                raise HTTPException(status_code=400, detail="inputs must be a non-empty array")
            return await run_generate_multi(
                items,
                [],
                project_id,
                feature_id,
                user_id,
                llm_provider,
                llm_model,
                min_tc,
                pref_types,
            )

        input_type = str(body.get("input_type") or "")
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        if not input_type or not project_id or not feature_id:
            raise HTTPException(status_code=400, detail="input_type, project_id, and feature_id are required")
        return await run_generate_core(
            input_type,
            project_id,
            feature_id,
            data,
            None,
            llm_provider,
            llm_model,
            user_id,
            min_test_cases=min_tc,
            preferred_test_types=pref_types,
        )

    form = await request.form()
    project_id = str(form.get("project_id") or "")
    feature_id = str(form.get("feature_id") or "")
    llm_provider = str(form.get("llm_provider")) if form.get("llm_provider") else None
    lm = form.get("llm_model")
    llm_model = str(lm).strip() if lm is not None and str(lm).strip() else None
    mtc = form.get("min_test_cases")
    if mtc is not None:
        try:
            min_tc = max(1, int(str(mtc)))
        except (TypeError, ValueError):
            min_tc = None
    pt = form.get("preferred_test_types")
    pref_types = [s.strip().lower() for s in str(pt).split(",") if s.strip()] if pt else None

    inputs_raw = form.get("inputs")
    if inputs_raw is not None and str(inputs_raw).strip():
        items = _parse_inputs_json(inputs_raw)
        if not items:
            raise HTTPException(status_code=400, detail="inputs must be a non-empty JSON array")
        for key, value in form.multi_items():
            if key == "files" and hasattr(value, "read"):
                files.append(value)  # type: ignore[arg-type]
        return await run_generate_multi(
            items,
            files,
            project_id,
            feature_id,
            user_id,
            llm_provider,
            llm_model,
            min_tc,
            pref_types,
        )

    input_type = str(form.get("input_type") or "")
    raw_data = form.get("data")
    if isinstance(raw_data, str):
        try:
            data = json.loads(raw_data) if raw_data.strip() else {}
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON in data field") from None
    else:
        data = {}
    f = form.get("file")
    if f is not None and hasattr(f, "read"):
        file = f  # type: ignore[assignment]

    if not input_type or not project_id or not feature_id:
        raise HTTPException(status_code=400, detail="input_type, project_id, and feature_id are required")

    return await run_generate_core(
        input_type,
        project_id,
        feature_id,
        data,
        file,
        llm_provider,
        llm_model,
        user_id,
        min_test_cases=min_tc,
        preferred_test_types=pref_types,
    )


@router.post("/generate/iterate", response_model=GenerateResponse)
async def iterate(
    body: GenerateIterateBody,
    user_id: str = Depends(get_current_user_id),
) -> GenerateResponse:
    single_feature_name: str = ""
    async with get_db() as db:
        proj = await project_repo.get_project(db, user_id, body.project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")

        if body.feature_id:
            feat = await feature_repo.get_feature(db, user_id, body.project_id, body.feature_id)
            if not feat:
                raise HTTPException(status_code=404, detail="Feature not found")
            single_feature_name = feat.name
            existing = await testcase_repo.list_test_cases_for_feature(db, body.project_id, body.feature_id)
        else:
            existing = await testcase_repo.list_test_cases_for_project(db, body.project_id)

        hashes = await testcase_repo.existing_hashes_for_project(db, body.project_id)

    project_description = proj.description or ""

    settings = get_effective_settings()
    try:
        generated = await generate_iterate(
            existing,
            body.instruction,
            body.feature_filter,
            body.type_filter,
            settings,
            provider_override=body.llm_provider,
            model_override=body.llm_model,
            project_description=project_description,
            min_test_cases=body.min_test_cases,
            preferred_test_types=body.preferred_test_types,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise map_upstream_exception("LLM error", e) from e

    iter_ref = f"Iterate: {(body.instruction or '')[:400]}"
    generated = [tc.model_copy(update={"source_ref": iter_ref}) for tc in generated]
    new_cases, skipped = filter_duplicates(generated, hashes)

    if not new_cases:
        async with get_db() as db:
            await input_repo.add_input_record(
                db,
                body.project_id,
                "iterate",
                "Iterate: no new cases after dedup",
                {"instruction": body.instruction},
                feature_id=body.feature_id,
            )
        return GenerateResponse(
            added_count=0,
            skipped_duplicate_count=skipped,
            test_cases=[],
            parsed_summary=body.instruction,
        )

    async with get_db() as db:
        feats = await feature_repo.list_features(db, user_id, body.project_id)
        by_name = {f.name.strip().lower(): f for f in feats}
        default_f = feats[0] if feats else None
        if not default_f:
            raise HTTPException(status_code=400, detail="Project has no features; add a feature first")

        if body.feature_id:
            new_cases = _normalize_feature_name(new_cases, single_feature_name)
            added = await testcase_repo.insert_test_cases(
                db, body.project_id, body.feature_id, single_feature_name, new_cases
            )
            if added:
                # Synthesize a text-type ParsedInput so the generation row carries the
                # iterate instruction as its single input.
                synthetic = ParsedInput(
                    source_type="text",
                    feature_name=single_feature_name,
                    raw_context=body.instruction or "",
                    metadata={},
                )
                await _persist_generation(
                    db=db,
                    project_id=body.project_id,
                    feature_id=body.feature_id,
                    trigger="iterate",
                    parsed=synthetic,
                    source_ref_override=iter_ref,
                    summary_override=f"Iterate: {(body.instruction or '')[:120]}",
                )
        else:
            from collections import defaultdict

            buckets: dict[str, list] = defaultdict(list)
            for tc in new_cases:
                key = (tc.feature or "").strip().lower()
                fobj = by_name.get(key) or default_f
                buckets[fobj.id].append(tc.model_copy(update={"feature": fobj.name}))

            added = []
            for fid, batch in buckets.items():
                fobj = next((x for x in feats if x.id == fid), default_f)
                batch_added = await testcase_repo.insert_test_cases(
                    db, body.project_id, fid, fobj.name, batch
                )
                added.extend(batch_added)
                if batch_added:
                    synthetic = ParsedInput(
                        source_type="text",
                        feature_name=fobj.name,
                        raw_context=body.instruction or "",
                        metadata={},
                    )
                    await _persist_generation(
                        db=db,
                        project_id=body.project_id,
                        feature_id=fid,
                        trigger="iterate",
                        parsed=synthetic,
                        source_ref_override=iter_ref,
                        summary_override=f"Iterate: {(body.instruction or '')[:120]}",
                    )

        await input_repo.add_input_record(
            db,
            body.project_id,
            "iterate",
            f"Iterate added {len(added)} case(s)",
            {"instruction": body.instruction},
            feature_id=body.feature_id,
        )

    return GenerateResponse(
        added_count=len(added),
        skipped_duplicate_count=skipped,
        test_cases=added,
        parsed_summary=body.instruction,
    )
