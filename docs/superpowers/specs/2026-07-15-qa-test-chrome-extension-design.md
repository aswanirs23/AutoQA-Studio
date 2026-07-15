# QA Test — Chrome Extension Design

**Date:** 2026-07-15
**Status:** Approved design, ready for implementation planning

## Summary

Package the `qa-test` senior-test-engineer skill as a standalone, generic
**Manifest V3 Chrome extension**. On any website, the user opens a side panel,
picks an LLM provider, and gets a structured QA bug report for the current tab.

The extension is fully self-contained: it runs entirely in the browser, needs no
server, and has **no dependency on the AutoQA Studio backend**. It lives in this
repo only for convenience (`qa-test-extension/`) and can be extracted to its own
repo at any time.

### Key decisions (from brainstorming)

- **Generic, not AutoQA-specific.** No calls to `backend/`, no coupling to this app.
- **Configurable multi-provider LLM.** User chooses Anthropic / OpenAI / Gemini and
  supplies that provider's API key. Analysis is done by calling the provider API
  directly from the extension's service worker.
- **Snapshot now, agentic later.** v1 analyzes captured page state (snapshot +
  manual capture-while-you-click). The architecture leaves room for a v2 autonomous
  agentic explorer that reuses the same capture + provider stack.
- **Vanilla MV3, no build step** for the shipped extension. Plain HTML/CSS/JS. The
  only Node tooling is a dev-time `tests/` folder for pure logic modules.

## Architecture

Four runtime pieces:

1. **Service worker** (`background.js`) — orchestrator. Receives a "run QA" message
   from the side panel, triggers capture in the target tab, assembles the prompt,
   calls the selected LLM provider adapter, and returns the report. Holds the API
   keys and performs all provider network calls (keeps keys out of page context and
   sidesteps page Content-Security-Policy).

2. **Content script** (`content/capture.js`) — reads the page and returns a
   structured context object (see Capture below). Injects a MAIN-world hook script
   to capture JS console errors.

3. **MAIN-world error hook** (`content/error-hooks.js`) — injected via
   `chrome.scripting.executeScript({world: 'MAIN'})`. Buffers `console.error`,
   `console.warn`, `window.onerror`, and `unhandledrejection` with timestamps, and
   exposes them for the content script to read.

4. **Side Panel** (`sidepanel/sidepanel.html` + `.js` + `.css`) — the UI: run
   button, capture-mode toggle, settings (provider / model / key), and the rendered
   report with export controls.

Network failures are captured separately in the service worker via
`chrome.webRequest`, recording 4xx/5xx responses and failed requests per tab. This
gets real status codes without page-context hooks.

## Capture modes (v1)

### Snapshot
Analyze the current page as-is. Produces:
- **Structure** — pruned DOM (tag, role, key attributes, trimmed text), capped in
  size to fit the token budget.
- **Forms** — each form's inputs with labels, `type`, `required`/validation attrs,
  placeholders.
- **Links** — href + text (flag empty/`#`/`javascript:` and duplicate targets).
- **Images** — `src` + presence/absence of `alt`.
- **Headings** — document heading outline (h1–h6 order/nesting).
- **Landmarks / ARIA** — roles, `aria-label`s, nav/main/header/footer.
- **Meta** — title, description, viewport, `lang` attribute, charset.
- **Buffered console errors** and **buffered network errors** accumulated for the tab.

### Capture-while-you-click
`content/recorder.js` attaches listeners for clicks, inputs, and submits. It logs
each interaction (element description, action, resulting URL/title change) and
correlates it with console/network errors that fire during the session. User hits
**Stop** → the recorded interaction log + errors + a final snapshot are sent for
analysis. This surfaces flow-level findings without an autonomous action loop.

**v2 note:** the agentic explorer reuses this capture stack and provider layer,
adding only an LLM-driven action loop (decide → execute via content script →
observe → repeat) with safety/budget guards. No rework of v1 components.

## LLM layer

- `lib/providers/index.js` selects an adapter by provider name.
- `lib/providers/anthropic.js`, `openai.js`, `gemini.js` — each exposes one
  `async run({ apiKey, model, system, content, image }) -> { text }`, normalizing
  the request and response shape per provider and setting the browser-access flag
  each API requires for direct browser calls (e.g. Anthropic's
  `anthropic-dangerous-direct-browser-access: true` header; OpenAI's browser-origin
  call; Gemini's API-key query call).
- Vision: a screenshot from `chrome.tabs.captureVisibleTab` is attached as an image
  content block **only** when the selected model supports vision; otherwise omitted.
- `lib/prompt.js` (pure) assembles the provider-agnostic message from the captured
  context and `qa-prompt.md`.
- `qa-prompt.md` — the `qa-test` SKILL content adapted from "agent with tools" to
  "you are given this captured page context; produce the report." It preserves the
  exact output sections (Test Summary, Bugs Found table, UX Drawbacks, What's
  Missing / Gaps, Major Drawbacks, Suggested Improvements, Top issues). It states
  the access mode used (Snapshot / Manual capture) and instructs the model to mark
  any finding it could not directly observe as **Suspected**.

### Token budgeting
`lib/extract.js` caps captured HTML/text sizes. When any section is truncated, the
prompt includes an explicit note so the model states the limitation in its report.

## Settings & key handling

- Stored in `chrome.storage.local`: `{ provider, models: {anthropic, openai,
  gemini}, apiKeys: {anthropic, openai, gemini} }`.
- Keys are extension-local (not readable by web pages) and are sent only to the
  chosen provider's API. Settings UI includes a **Clear keys** button and a visible
  note that keys are stored locally in the browser.

## Data flow

1. User opens the side panel on a tab; configures provider/key/model once.
2. User selects a mode and clicks **Run QA test** (snapshot) or **Start recording** →
   interacts → **Stop** (capture mode).
3. Side panel → service worker: `runQa` message with `tabId` + `mode`.
4. Service worker injects/runs capture in the tab, collects the structured context +
   buffered console errors + buffered network errors (+ optional screenshot).
5. Service worker builds the prompt via `lib/prompt.js` and calls the selected
   provider adapter.
6. Report text returns to the side panel, rendered as Markdown with a proper HTML
   bug table; user can **Copy** or **Download .md**.

## Error handling

- **No key set** → panel shows a nudge to open settings.
- **Restricted pages** (`chrome://`, Chrome Web Store, PDF viewer) → content-script
  injection fails → friendly "can't test this page" message.
- **LLM API errors** (401 / 429 / network) → surface the provider's error message
  clearly, keep the captured context in memory, and allow retry without recapturing.
- **Oversized capture** → truncate and include a visible note in the report.

## Testing

- Pure modules — `lib/extract.js`, `lib/prompt.js`, and each provider's
  request-builder / response-normalizer — are framework-free and unit-tested with
  Node's built-in test runner (`node --test`) against fixtures and a mocked `fetch`.
- The shipped extension itself stays no-build; `tests/` is a dev-time convenience
  and is not part of the packaged extension.
- Manual acceptance: load unpacked, run against a small set of target sites
  (a form-heavy page, a page with console errors, a restricted page).

## File structure

```
qa-test-extension/
  manifest.json
  background.js
  content/
    capture.js         # content script: DOM -> structured context
    error-hooks.js      # MAIN-world console/error hook
    recorder.js         # capture-while-you-click interaction log
  sidepanel/
    sidepanel.html
    sidepanel.js
    sidepanel.css
  lib/
    extract.js          # pure: raw capture -> structured, size-capped context
    prompt.js           # pure: context + qa-prompt.md -> provider message
    providers/
      index.js
      anthropic.js
      openai.js
      gemini.js
  qa-prompt.md          # adapted qa-test system prompt
  icons/                # 16/32/48/128 px
  README.md             # load-unpacked + usage instructions
  tests/                # node --test for pure modules
```

## Manifest highlights

- MV3, `side_panel`, service-worker background.
- Permissions: `activeTab`, `scripting`, `storage`, `sidePanel`, `tabs`
  (for `captureVisibleTab`), `webRequest`.
- `host_permissions`: `<all_urls>` (content capture) + provider API hosts
  (`https://api.anthropic.com/*`, `https://api.openai.com/*`,
  `https://generativelanguage.googleapis.com/*`).

## Out of scope (v1)

- Autonomous agentic exploration (v2 — see note above).
- CDP/`chrome.debugger` deep capture (candidate for v2 agentic mode).
- Any AutoQA Studio backend integration.
- Firefox packaging (design is Chromium-compatible: Chrome/Edge/Brave/Arc).
