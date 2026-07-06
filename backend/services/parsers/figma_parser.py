"""Figma file / frame parser via Figma REST API.

Steps:
1. Parse figma_url → file_key and optional node-id (for deep links).
2. GET /v1/files/{key}/nodes or full file with X-Figma-Token.
3. Walk document JSON: collect FRAME names, component names, TEXT content.
4. Optionally render top-level frames via /v1/images and run each PNG through the
   shared vision pipeline (same one screenshot_parser uses) for richer context.
5. Build raw_context string and fill screens / ui_elements lists.
"""

import asyncio
import base64
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from starlette.datastructures import UploadFile

from backend.db import fetch_one, get_db

logger = logging.getLogger(__name__)

# How long a Figma fetch is reused before re-fetching from Figma.
CACHE_TTL_SECONDS = 6 * 60 * 60

from backend.config import get_effective_settings
from backend.services.parsers.base import BaseParser, InputFieldDef, ParsedInput, ParserMeta
from backend.services.parsers.registry import ParserRegistry
from backend.services.parsers.screenshot_parser import describe_image_data_url


DEFAULT_MAX_VISION_FRAMES = 4
HARD_MAX_VISION_FRAMES = 12

# Spec extraction limits — a deep file can have thousands of nodes; keep prompt sane.
MAX_SPEC_LINES = 600
MAX_SPEC_DEPTH = 14
# Decorative shape types that produce noise without much QA value.
_SPEC_SKIP_TYPES = {"VECTOR", "BOOLEAN_OPERATION", "STAR", "REGULAR_POLYGON", "LINE"}


def _parse_figma_url(url: str) -> tuple[str, str | None]:
    """Return (file_key for API, node_id or None). Node id uses colon form e.g. 1:2.

    Supports branch URLs: .../file/{key}/branch/{branchKey}/... → use branchKey as file key for API.
    Supports /design/ and /file/ and /board/ (FigJam uses same files API with file key).
    """
    url = url.strip()
    # Branch: /file/ABC/branch/DEF/... → API file key is DEF
    branch_m = re.search(
        r"figma\.com/(?:file|design|board)/([a-zA-Z0-9]+)/branch/([a-zA-Z0-9]+)",
        url,
    )
    if branch_m:
        file_key = branch_m.group(2)
    else:
        m = re.search(r"figma\.com/(?:file|design|board)/([a-zA-Z0-9]+)", url)
        if not m:
            raise ValueError("Could not extract Figma file key from URL")
        file_key = m.group(1)
    node_m = re.search(r"[?&]node-id=([^&]+)", url)
    node_id = None
    if node_m:
        raw = node_m.group(1).replace("-", ":")
        node_id = raw
    return file_key, node_id


def _collect_node_info(node: dict[str, Any], depth: int = 0, max_depth: int = 12) -> tuple[list[str], list[str], list[str]]:
    """Collect screen names (FRAME), UI-like names, and text content."""
    screens: list[str] = []
    elements: list[str] = []
    texts: list[str] = []
    if depth > max_depth:
        return screens, elements, texts
    ntype = node.get("type", "")
    name = node.get("name", "")
    if ntype in ("FRAME", "COMPONENT", "INSTANCE", "GROUP"):
        if name and ntype == "FRAME":
            screens.append(name)
        elif name:
            elements.append(f"{ntype}: {name}")
    if ntype == "TEXT" and name:
        texts.append(name)
    for ch in node.get("children") or []:
        s, e, t = _collect_node_info(ch, depth + 1, max_depth)
        screens.extend(s)
        elements.extend(e)
        texts.extend(t)
    return screens, elements, texts


def _collect_top_frames(document: dict[str, Any]) -> list[tuple[str, str]]:
    """(node_id, name) for FRAMEs that are direct children of pages — i.e. the screens."""
    out: list[tuple[str, str]] = []
    for page in document.get("children") or []:
        for ch in page.get("children") or []:
            if ch.get("type") == "FRAME":
                nid = str(ch.get("id") or "").strip()
                nm = str(ch.get("name") or "").strip()
                if nid:
                    out.append((nid, nm))
    return out


def _rgba_to_css(color: dict[str, Any], opacity: float = 1.0) -> str:
    """Figma colors are 0..1 floats; emit #RRGGBB or rgba(...) when alpha < 1."""
    if not color:
        return ""
    r = int(round(float(color.get("r", 0)) * 255))
    g = int(round(float(color.get("g", 0)) * 255))
    b = int(round(float(color.get("b", 0)) * 255))
    a = float(color.get("a", 1.0)) * float(opacity)
    if a >= 0.999:
        return f"#{r:02X}{g:02X}{b:02X}"
    return f"rgba({r},{g},{b},{a:.2f})"


def _format_paints(paints: list[dict[str, Any]] | None) -> str:
    """Solid colors, gradients, and image fills — used for both fills and strokes."""
    if not paints:
        return ""
    out: list[str] = []
    for p in paints:
        if p.get("visible") is False:
            continue
        ptype = p.get("type", "")
        op = float(p.get("opacity", 1.0))
        if ptype == "SOLID":
            out.append(_rgba_to_css(p.get("color") or {}, op))
        elif ptype.startswith("GRADIENT"):
            stops = p.get("gradientStops") or []
            colors = [_rgba_to_css(s.get("color") or {}) for s in stops if s.get("color")]
            if colors:
                out.append(f"{ptype.replace('GRADIENT_', '').lower()}-grad({', '.join(colors)})")
        elif ptype == "IMAGE":
            out.append("image-fill")
    return ", ".join(x for x in out if x)


def _format_text_style(style: dict[str, Any]) -> str:
    if not style:
        return ""
    parts: list[str] = []
    if style.get("fontFamily"):
        parts.append(str(style["fontFamily"]))
    if style.get("fontSize") is not None:
        parts.append(f"{style['fontSize']}px")
    if style.get("fontWeight") is not None:
        parts.append(f"w{style['fontWeight']}")
    lh_px = style.get("lineHeightPx")
    lh_pct = style.get("lineHeightPercent")
    if lh_px:
        parts.append(f"lh:{float(lh_px):.0f}px")
    elif lh_pct:
        parts.append(f"lh:{float(lh_pct):.0f}%")
    ls = style.get("letterSpacing")
    if ls is not None and float(ls) != 0:
        parts.append(f"ls:{float(ls):.2f}")
    case = style.get("textCase")
    if case and case != "ORIGINAL":
        parts.append(case.lower())
    deco = style.get("textDecoration")
    if deco and deco != "NONE":
        parts.append(deco.lower())
    align_h = style.get("textAlignHorizontal")
    if align_h and align_h != "LEFT":
        parts.append(f"align-{align_h.lower()}")
    align_v = style.get("textAlignVertical")
    if align_v and align_v != "TOP":
        parts.append(f"valign-{align_v.lower()}")
    return " / ".join(parts)


def _format_auto_layout(node: dict[str, Any]) -> str:
    mode = node.get("layoutMode")
    if not mode or mode == "NONE":
        return ""
    parts = [f"auto-layout:{mode.lower()}"]
    if node.get("itemSpacing") is not None:
        parts.append(f"gap:{node['itemSpacing']}")
    pad = (
        node.get("paddingTop", 0),
        node.get("paddingRight", 0),
        node.get("paddingBottom", 0),
        node.get("paddingLeft", 0),
    )
    if any(pad):
        parts.append(f"padding:{pad[0]}/{pad[1]}/{pad[2]}/{pad[3]}")
    if node.get("primaryAxisAlignItems"):
        parts.append(f"main:{node['primaryAxisAlignItems'].lower()}")
    if node.get("counterAxisAlignItems"):
        parts.append(f"cross:{node['counterAxisAlignItems'].lower()}")
    if node.get("primaryAxisSizingMode") == "AUTO":
        parts.append("hug-main")
    if node.get("counterAxisSizingMode") == "AUTO":
        parts.append("hug-cross")
    return " ".join(parts)


def _format_effects(effects: list[dict[str, Any]] | None) -> str:
    if not effects:
        return ""
    out: list[str] = []
    for e in effects:
        if e.get("visible") is False:
            continue
        t = e.get("type", "")
        if t in ("DROP_SHADOW", "INNER_SHADOW"):
            color = _rgba_to_css(e.get("color") or {})
            offset = e.get("offset") or {}
            out.append(
                f"{t.lower()}(x:{offset.get('x', 0)},y:{offset.get('y', 0)},"
                f"r:{e.get('radius', 0)},spread:{e.get('spread', 0)},{color})"
            )
        elif t in ("LAYER_BLUR", "BACKGROUND_BLUR"):
            out.append(f"{t.lower()}(r:{e.get('radius', 0)})")
    return ", ".join(out)


def _format_component_props(props: dict[str, Any] | None) -> str:
    if not props:
        return ""
    items: list[str] = []
    for k, v in props.items():
        val = v.get("value") if isinstance(v, dict) else v
        items.append(f"{k}={val}")
    return ", ".join(items)


def _format_corner_radius(node: dict[str, Any]) -> str:
    cr = node.get("cornerRadius")
    if isinstance(cr, (int, float)) and cr:
        return f"radius:{cr}"
    rr = node.get("rectangleCornerRadii")
    if isinstance(rr, list) and any(rr):
        return "radius:" + "/".join(str(x) for x in rr)
    return ""


def _format_constraints(node: dict[str, Any]) -> str:
    c = node.get("constraints") or {}
    if not c:
        return ""
    h = c.get("horizontal", "")
    v = c.get("vertical", "")
    if not h and not v:
        return ""
    return f"constraints:{h.lower()}/{v.lower()}"


def _format_interactions(node: dict[str, Any]) -> str:
    """Prototype interactions: trigger → action (e.g. ON_CLICK → NAVIGATE)."""
    inters = node.get("interactions") or []
    if not inters:
        return ""
    parts: list[str] = []
    for it in inters:
        trig = (it.get("trigger") or {}).get("type", "")
        actions = it.get("actions") or []
        for a in actions:
            atype = a.get("type", "")
            dest = a.get("destinationId") or ""
            parts.append(f"{trig}->{atype}{f'({dest})' if dest else ''}")
    return "interactions:[" + "; ".join(parts) + "]" if parts else ""


def _extract_specs(
    node: dict[str, Any],
    lines: list[str],
    depth: int = 0,
) -> None:
    """Walk the document and emit one indented spec line per meaningful node."""
    if len(lines) >= MAX_SPEC_LINES or depth > MAX_SPEC_DEPTH:
        return
    if node.get("visible") is False:
        return

    ntype = node.get("type", "")
    name = str(node.get("name") or "").strip()

    if ntype not in _SPEC_SKIP_TYPES:
        attrs: list[str] = []

        bbox = node.get("absoluteBoundingBox") or {}
        w = bbox.get("width")
        h = bbox.get("height")
        if w is not None and h is not None:
            attrs.append(f"size:{float(w):.0f}x{float(h):.0f}")

        if ntype == "TEXT":
            chars = str(node.get("characters") or "").replace("\n", " ")
            ts = _format_text_style(node.get("style") or {})
            if ts:
                attrs.append(ts)
            color = _format_paints(node.get("fills"))
            if color:
                attrs.append(f"color:{color}")
            label = f'TEXT "{chars[:120]}"'
        else:
            fill = _format_paints(node.get("fills"))
            if fill:
                attrs.append(f"bg:{fill}")
            stroke = _format_paints(node.get("strokes"))
            if stroke:
                sw = node.get("strokeWeight")
                attrs.append(f"stroke:{stroke}" + (f"@{sw}px" if sw else ""))
            cr = _format_corner_radius(node)
            if cr:
                attrs.append(cr)
            al = _format_auto_layout(node)
            if al:
                attrs.append(al)
            eff = _format_effects(node.get("effects"))
            if eff:
                attrs.append(f"effects:{eff}")
            op = node.get("opacity")
            if op is not None and float(op) < 1.0:
                attrs.append(f"opacity:{float(op):.2f}")
            cprops = _format_component_props(node.get("componentProperties"))
            if cprops:
                attrs.append(f"variants:{cprops}")
            cstr = _format_constraints(node)
            if cstr:
                attrs.append(cstr)
            inter = _format_interactions(node)
            if inter:
                attrs.append(inter)

            if ntype == "COMPONENT_SET":
                defs = node.get("componentPropertyDefinitions") or {}
                axes: list[str] = []
                for k, v in defs.items():
                    ptype = v.get("type", "")
                    options = v.get("variantOptions")
                    if options:
                        axes.append(f"{k}:[{','.join(options)}]")
                    else:
                        axes.append(f"{k}:{ptype}")
                if axes:
                    attrs.append("variant-axes:{" + "; ".join(axes) + "}")

            label = f'{ntype} "{name}"' if name else ntype

        # Skip nodes that have neither a meaningful name nor any attribute info.
        # Container groups with only a child fan-out clutter the prompt otherwise.
        if attrs or ntype == "TEXT" or (name and ntype in ("FRAME", "COMPONENT", "INSTANCE", "COMPONENT_SET")):
            indent = "  " * depth
            if attrs:
                lines.append(f"{indent}{label} — {' / '.join(attrs)}")
            else:
                lines.append(f"{indent}{label}")

    for ch in node.get("children") or []:
        if len(lines) >= MAX_SPEC_LINES:
            lines.append("… (spec output truncated)")
            return
        _extract_specs(ch, lines, depth + 1)


def _truthy(value: Any) -> bool:
    """Accept bool, "true"/"1"/"yes"/"on" (any case), and 0/1 numerics."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if value is None:
        return False
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _coerce_int(value: Any, default: int) -> int:
    """Form fields arrive as strings; tolerate empty / bad input by falling back to default."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def _render_frames(
    client: httpx.AsyncClient,
    file_key: str,
    node_ids: list[str],
    headers: dict[str, str],
) -> dict[str, str]:
    """Call /v1/images to get signed PNG URLs keyed by node id. Skips silently on failure."""
    if not node_ids:
        return {}
    try:
        r = await client.get(
            f"https://api.figma.com/v1/images/{file_key}",
            params={"ids": ",".join(node_ids), "format": "png", "scale": 1},
            headers=headers,
        )
        r.raise_for_status()
        body = r.json()
    except (httpx.HTTPError, ValueError):
        return {}
    images = body.get("images") or {}
    return {k: v for k, v in images.items() if isinstance(v, str) and v}


async def _download_as_data_url(client: httpx.AsyncClient, url: str) -> str | None:
    """Fetch the rendered PNG and turn it into a data: URL. Returns None on any error."""
    try:
        r = await client.get(url)
        r.raise_for_status()
    except httpx.HTTPError:
        return None
    mime = r.headers.get("content-type", "image/png").split(";")[0].strip() or "image/png"
    b64 = base64.standard_b64encode(r.content).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _build_cache_key(file_key: str, node_id: str | None, max_vision_frames: int) -> str:
    return f"{file_key}|{node_id or ''}|{max_vision_frames}"


def _parse_cached_at(value: str) -> datetime | None:
    """SQLite stores 'YYYY-MM-DD HH:MM:SS' from datetime('now') in UTC."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace(" ", "T"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


async def _load_cached(cache_key: str) -> tuple[ParsedInput, datetime] | None:
    """Return (ParsedInput, cached_at_utc) if cached and within TTL, else None."""
    try:
        async with get_db() as db:
            row = await fetch_one(
                db,
                "SELECT parsed_json, cached_at FROM figma_cache WHERE cache_key = ?",
                (cache_key,),
            )
    except Exception as e:
        logger.warning("figma cache lookup failed: %s", e)
        return None
    if not row:
        return None
    cached_at = _parse_cached_at(row["cached_at"])
    if cached_at is None:
        return None
    age = (datetime.now(tz=timezone.utc) - cached_at).total_seconds()
    if age > CACHE_TTL_SECONDS:
        return None
    try:
        parsed = ParsedInput.model_validate_json(row["parsed_json"])
    except Exception as e:
        logger.warning("figma cache parse failed: %s", e)
        return None
    return parsed, cached_at


async def _save_cached(
    cache_key: str,
    file_key: str,
    node_id: str | None,
    max_vision_frames: int,
    url: str,
    parsed: ParsedInput,
) -> None:
    payload = parsed.model_dump_json()
    try:
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO figma_cache (cache_key, file_key, node_id, max_vision_frames, url, parsed_json, cached_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(cache_key) DO UPDATE SET
                    parsed_json = excluded.parsed_json,
                    cached_at = excluded.cached_at,
                    url = excluded.url
                """,
                (cache_key, file_key, node_id or "", int(max_vision_frames), url, payload),
            )
    except Exception as e:
        logger.warning("figma cache save failed: %s", e)


async def _analyze_frame(
    client: httpx.AsyncClient,
    name: str,
    image_url: str,
    settings: Any,
    provider_override: str | None,
    model_override: str | None,
) -> tuple[str, str] | None:
    """Download a rendered frame and run it through the shared vision describer."""
    data_url = await _download_as_data_url(client, image_url)
    if not data_url:
        return None
    try:
        text = await describe_image_data_url(
            data_url,
            settings,
            provider_override=provider_override,
            model_override=model_override,
        )
    except Exception:
        return None
    if not text or not text.strip():
        return None
    return name, text.strip()


class FigmaParser(BaseParser):
    meta = ParserMeta(
        name="figma",
        display_name="Figma",
        description="Paste a Figma design URL; we fetch structure, text, and (optionally) render frames for vision analysis.",
        input_fields=[
            InputFieldDef(
                name="feature_name",
                type="text",
                label="Feature name (optional)",
                placeholder="e.g. checkout",
                required=False,
            ),
            InputFieldDef(
                name="figma_url",
                type="url",
                label="Figma URL",
                placeholder="https://www.figma.com/design/...",
                required=True,
            ),
            InputFieldDef(
                name="max_vision_frames",
                type="number",
                label="Frames to analyze with vision (0 = skip)",
                placeholder=str(DEFAULT_MAX_VISION_FRAMES),
                required=False,
            ),
            InputFieldDef(
                name="force_refresh",
                type="checkbox",
                label="Force refresh from Figma (skip cache)",
                required=False,
            ),
        ],
        accepts_file=False,
    )

    async def parse(self, data: dict[str, Any], file: UploadFile | None) -> ParsedInput:
        settings = get_effective_settings()
        token = settings.figma_access_token
        if not token:
            raise ValueError("FIGMA_ACCESS_TOKEN is not set (add to .env or API keys in the UI)")

        url = str(data.get("figma_url") or data.get("url") or "").strip()
        if not url:
            raise ValueError("figma_url is required")

        file_key, node_id = _parse_figma_url(url)
        feature = str(data.get("feature_name") or "").strip()

        max_vision = _coerce_int(data.get("max_vision_frames"), DEFAULT_MAX_VISION_FRAMES)
        max_vision = max(0, min(max_vision, HARD_MAX_VISION_FRAMES))

        # Injected by generate router so vision matches UI / request
        llm_override = data.pop("_llm_provider", None) if isinstance(data, dict) else None
        model_override = data.pop("_llm_model", None) if isinstance(data, dict) else None

        force_refresh = _truthy(data.get("force_refresh"))
        cache_key = _build_cache_key(file_key, node_id, max_vision)
        if not force_refresh:
            hit = await _load_cached(cache_key)
            if hit is not None:
                cached, cached_at = hit
                age_min = (datetime.now(tz=timezone.utc) - cached_at).total_seconds() / 60.0
                logger.info("figma: cache hit key=%s age=%.1fm", cache_key, age_min)
                meta = dict(cached.metadata or {})
                meta["from_cache"] = True
                meta["cache_age_seconds"] = int(age_min * 60)
                return cached.model_copy(update={"metadata": meta})

        headers = {"X-Figma-Token": token}
        t_start = time.perf_counter()
        logger.info("figma: start key=%s node=%s vision=%d (cache miss%s)", file_key, node_id, max_vision, ", forced" if force_refresh else "")

        async with httpx.AsyncClient(timeout=60.0) as client:
            t_fetch_a = time.perf_counter()
            if node_id:
                r = await client.get(
                    f"https://api.figma.com/v1/files/{file_key}/nodes",
                    params={"ids": node_id},
                    headers=headers,
                )
            else:
                r = await client.get(
                    f"https://api.figma.com/v1/files/{file_key}",
                    headers=headers,
                )
            r.raise_for_status()
            payload = r.json()
            logger.info("figma: file fetch %.2fs (%d bytes)", time.perf_counter() - t_fetch_a, len(r.content))

            t_walk_a = time.perf_counter()
            screens: list[str] = []
            elements: list[str] = []
            texts: list[str] = []
            spec_lines: list[str] = []
            frame_targets: list[tuple[str, str]] = []  # (node_id, name) for /v1/images

            if node_id and "nodes" in payload:
                for nid, nodedata in (payload.get("nodes") or {}).items():
                    doc = nodedata.get("document") or {}
                    s, e, t = _collect_node_info(doc)
                    screens.extend(s)
                    elements.extend(e)
                    texts.extend(t)
                    _extract_specs(doc, spec_lines)
                    # The deep-linked node itself is the screen we want to render.
                    nm = str(doc.get("name") or "").strip() or nid
                    frame_targets.append((nid, nm))
            else:
                doc = payload.get("document") or {}
                for page in doc.get("children") or []:
                    s, e, t = _collect_node_info(page)
                    screens.extend(s)
                    elements.extend(e)
                    texts.extend(t)
                    _extract_specs(page, spec_lines)
                frame_targets = _collect_top_frames(doc)

            # De-dupe preserve order
            def _uniq(xs: list[str]) -> list[str]:
                seen: set[str] = set()
                out: list[str] = []
                for x in xs:
                    if x not in seen:
                        seen.add(x)
                        out.append(x)
                return out

            screens = _uniq(screens)
            elements = _uniq(elements)
            texts = _uniq(texts)
            logger.info(
                "figma: tree walk %.2fs (screens=%d elements=%d texts=%d spec=%d frames=%d)",
                time.perf_counter() - t_walk_a,
                len(screens), len(elements), len(texts), len(spec_lines), len(frame_targets),
            )

            vision_blocks: list[tuple[str, str]] = []
            analyzed_frames: list[str] = []
            if max_vision > 0 and frame_targets:
                selected = frame_targets[:max_vision]
                t_render_a = time.perf_counter()
                images = await _render_frames(client, file_key, [nid for nid, _ in selected], headers)
                logger.info(
                    "figma: image render %.2fs (requested=%d returned=%d)",
                    time.perf_counter() - t_render_a, len(selected), len(images),
                )
                t_vision_a = time.perf_counter()
                tasks = [
                    _analyze_frame(client, name, images[nid], settings, llm_override, model_override)
                    for nid, name in selected
                    if images.get(nid)
                ]
                if tasks:
                    results = await asyncio.gather(*tasks, return_exceptions=False)
                    for res in results:
                        if res is None:
                            continue
                        name, text = res
                        vision_blocks.append((name, text))
                        analyzed_frames.append(name)
                logger.info(
                    "figma: vision %.2fs (analyzed=%d/%d)",
                    time.perf_counter() - t_vision_a, len(analyzed_frames), len(tasks),
                )

        logger.info("figma: parser total %.2fs", time.perf_counter() - t_start)

        raw_lines = [
            f"Figma file key: {file_key}",
            f"Source URL: {url}",
        ]
        if feature:
            raw_lines.append(f"Feature: {feature}")
        if screens:
            raw_lines.append("Screens / frames: " + "; ".join(screens[:50]))
        if elements:
            raw_lines.append("UI nodes: " + "; ".join(elements[:80]))
        if texts:
            raw_lines.append("Text content: " + " | ".join(texts[:100]))
        if spec_lines:
            raw_lines.append("")
            raw_lines.append(
                "Design specs (per node — sizes in px, colors in hex, fontWeight prefixed w):"
            )
            raw_lines.extend(spec_lines)
        if vision_blocks:
            raw_lines.append("")
            raw_lines.append("Vision analysis of rendered frames:")
            for name, text in vision_blocks:
                raw_lines.append(f"--- Frame: {name} ---")
                raw_lines.append(text)
        raw_context = "\n".join(raw_lines)

        result = ParsedInput(
            source_type="figma",
            feature_name=feature or (screens[0] if screens else "figma"),
            screens=screens[:30],
            ui_elements=elements[:100],
            user_actions=[],
            business_rules=[],
            raw_context=raw_context,
            metadata={
                "file_key": file_key,
                "node_id": node_id,
                "url": url,
                "vision_frames_analyzed": analyzed_frames,
                "spec_lines": len(spec_lines),
                "from_cache": False,
            },
        )
        await _save_cached(cache_key, file_key, node_id, max_vision, url, result)
        return result


# Self-register on import
ParserRegistry.register(FigmaParser())
