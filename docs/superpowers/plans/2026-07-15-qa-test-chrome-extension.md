# QA Test Chrome Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a standalone Manifest V3 Chrome extension — in its **own git repository**, fully independent of AutoQA Studio — that QA-tests the current browser tab using a user-configured LLM provider and produces the `qa-test` senior-test-engineer bug report.

**Architecture:** A no-build MV3 extension with a service-worker orchestrator, a content script that captures page context, a MAIN-world error hook, `chrome.webRequest` network capture, and a Side Panel UI. All LLM logic and data shaping lives in framework-free `lib/` modules that are unit-tested with Node's built-in test runner; the shipped extension itself is plain browser JS with no bundler.

**Tech Stack:** Manifest V3, vanilla JS (ES modules), `chrome.sidePanel` / `scripting` / `webRequest` / `storage` / `tabs` APIs, Node 20 `node --test` for dev-time unit tests. No runtime dependencies.

## Global Constraints

- **This is a brand-new, standalone git repository** at `/home/aswanirs/Downloads/qa-test-extension/`. It shares **no** code, files, imports, or git history with AutoQA Studio. The extension's root **is** the repo root — all paths below are relative to it.
- Product is **generic** — no reference to AutoQA Studio anywhere.
- Manifest V3 only. Single service worker; no persistent background page.
- API keys stored only in `chrome.storage.local`; sent only to the selected provider's API; never logged.
- Providers supported: `anthropic`, `openai`, `gemini`. Provider names are these exact lowercase strings everywhere.
- No build step for the shipped extension. `tests/` is dev-time only and is not packaged.
- All git commands run **inside the new repo** (`cd /home/aswanirs/Downloads/qa-test-extension`). Do not commit any of these files into the AutoQA Studio repo.
- Node test command everywhere: `node --test tests/`.

---

## File Structure (repo root)

```
qa-test-extension/               # repo root
  manifest.json
  background.js            # service worker: orchestration, webRequest capture
  content/
    capture.js             # content script: live DOM -> RawCapture
    error-hooks.js         # MAIN-world: buffers console/window errors
    recorder.js            # capture-while-you-click interaction log
  sidepanel/
    sidepanel.html
    sidepanel.js
    sidepanel.css
  lib/
    extract.js             # pure: RawCapture + netErrors -> capped Context
    prompt.js              # pure: Context -> {system, content}
    markdown.js            # pure: minimal markdown -> safe HTML for the report
    providers/
      index.js             # getProvider(name)
      anthropic.js         # buildRequest / parseResponse / run
      openai.js
      gemini.js
  qa-prompt.md             # adapted qa-test system prompt
  icons/                   # 16/32/48/128 png
  README.md
  docs/                    # copied design spec + this plan (self-documenting)
  tests/
    extract.test.js
    prompt.test.js
    providers.test.js
    markdown.test.js
    fixtures/
      raw-capture.js       # sample RawCapture + netErrors
```

### Canonical data shapes (used across tasks)

```js
// RawCapture — produced by content/capture.js, consumed by lib/extract.js
{
  url: string, title: string, lang: string, charset: string,
  viewport: string|null, description: string|null,
  headings: [{ level: number, text: string }],
  landmarks: [{ role: string, label: string|null, tag: string }],
  forms: [{ name: string|null, action: string|null, method: string,
            inputs: [{ tag: string, type: string, name: string|null,
                       label: string|null, required: boolean,
                       placeholder: string|null, pattern: string|null }] }],
  links: [{ text: string, href: string }],
  images: [{ src: string, alt: string|null }],   // alt null == missing attribute
  buttons: [{ text: string, disabled: boolean }],
  domOutline: string,                              // trimmed structural HTML/text
  consoleErrors: [{ level: string, message: string, time: number }],
  interactions?: [{ action: string, target: string, detail: string|null, time: number }]
}

// NetworkError — produced by background webRequest capture
{ url: string, status: number, method: string, type: string, time: number }

// Context — produced by lib/extract.js
{ ...normalized RawCapture fields, networkErrors: NetworkError[],
  truncated: { domOutline: boolean, links: boolean, images: boolean } }
```

---

### Task 0: Initialize the standalone repository

**Files:** none yet (repo scaffolding).

**Interfaces:** Produces an empty git repo at `/home/aswanirs/Downloads/qa-test-extension/` that all later tasks commit into.

- [ ] **Step 1: Create the repo and directory skeleton**

Run:
```bash
mkdir -p /home/aswanirs/Downloads/qa-test-extension/{content,sidepanel,lib/providers,icons,docs,tests/fixtures}
cd /home/aswanirs/Downloads/qa-test-extension
git init
```
Expected: `Initialized empty Git repository`.

- [ ] **Step 2: Add a `.gitignore`**

Create `.gitignore`:
```
node_modules/
*.log
.DS_Store
```

- [ ] **Step 3: Copy the design docs into the new repo**

Run:
```bash
cp /home/aswanirs/Downloads/AutoQAstudio/docs/superpowers/specs/2026-07-15-qa-test-chrome-extension-design.md /home/aswanirs/Downloads/qa-test-extension/docs/design.md
cp /home/aswanirs/Downloads/AutoQAstudio/docs/superpowers/plans/2026-07-15-qa-test-chrome-extension.md /home/aswanirs/Downloads/qa-test-extension/docs/plan.md
```

- [ ] **Step 4: Commit**

```bash
cd /home/aswanirs/Downloads/qa-test-extension
git add .gitignore docs
git commit -m "chore: initialize qa-test extension repo with design docs"
```

---

### Task 1: Extension scaffold that loads and opens a side panel

**Files:**
- Create: `manifest.json`
- Create: `background.js`
- Create: `sidepanel/sidepanel.html`
- Create: `sidepanel/sidepanel.css`
- Create: `sidepanel/sidepanel.js`
- Create: `icons/icon16.png`, `icon32.png`, `icon48.png`, `icon128.png`

**Interfaces:**
- Produces: a loadable extension whose toolbar icon opens the side panel.

- [ ] **Step 1: Write `manifest.json`**

```json
{
  "manifest_version": 3,
  "name": "QA Test — Senior Test Engineer",
  "version": "0.1.0",
  "description": "QA-test the current tab with an LLM and get a structured bug report.",
  "permissions": ["activeTab", "scripting", "storage", "sidePanel", "tabs", "webRequest"],
  "host_permissions": [
    "<all_urls>",
    "https://api.anthropic.com/*",
    "https://api.openai.com/*",
    "https://generativelanguage.googleapis.com/*"
  ],
  "background": { "service_worker": "background.js", "type": "module" },
  "side_panel": { "default_path": "sidepanel/sidepanel.html" },
  "action": { "default_title": "QA Test" },
  "icons": { "16": "icons/icon16.png", "32": "icons/icon32.png", "48": "icons/icon48.png", "128": "icons/icon128.png" }
}
```

- [ ] **Step 2: Write minimal `background.js`**

```js
// Open the side panel when the toolbar icon is clicked.
chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});
```

- [ ] **Step 3: Write `sidepanel/sidepanel.html` shell**

```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <link rel="stylesheet" href="sidepanel.css" />
</head>
<body>
  <header><h1>QA Test</h1></header>
  <main id="app"><p id="status">Loaded.</p></main>
  <script type="module" src="sidepanel.js"></script>
</body>
</html>
```

- [ ] **Step 4: Write `sidepanel/sidepanel.css` (minimal)**

```css
body { font: 13px/1.5 system-ui, sans-serif; margin: 0; padding: 12px; }
header h1 { font-size: 15px; margin: 0 0 8px; }
button { font: inherit; padding: 6px 10px; cursor: pointer; }
```

- [ ] **Step 5: Write `sidepanel/sidepanel.js` placeholder**

```js
document.getElementById("status").textContent = "QA Test panel ready.";
```

- [ ] **Step 6: Create placeholder icons**

Run:
```bash
cd /home/aswanirs/Downloads/qa-test-extension && for s in 16 32 48 128; do printf '\x89PNG\r\n\x1a\n' > icons/icon$s.png; done
```
Note: these are placeholder bytes so the manifest loads; replace with real PNGs before publishing. (If Chrome rejects them, generate solid-color PNGs of each size with any tool.)

- [ ] **Step 7: Manual verification — load unpacked**

1. Open `chrome://extensions`, enable Developer mode, "Load unpacked" → select the repo root `/home/aswanirs/Downloads/qa-test-extension/`.
Expected: extension appears with no manifest errors.
2. Click the toolbar icon.
Expected: side panel opens showing "QA Test panel ready."

- [ ] **Step 8: Commit**

```bash
cd /home/aswanirs/Downloads/qa-test-extension
git add manifest.json background.js sidepanel icons
git commit -m "feat: scaffold MV3 extension with side panel"
```

---

### Task 2: `lib/extract.js` — normalize and size-cap captured context

**Files:**
- Create: `lib/extract.js`
- Create: `tests/fixtures/raw-capture.js`
- Test: `tests/extract.test.js`

**Interfaces:**
- Consumes: `RawCapture`, `NetworkError[]` (shapes above).
- Produces: `buildContext(raw, networkErrors, opts = {}) -> Context`. Defaults: `maxHtml=12000`, `maxItems=200`. Sets `truncated.{domOutline,links,images}` when a cap trims data. Missing/undefined arrays default to `[]`.

- [ ] **Step 1: Write the fixture**

```js
// tests/fixtures/raw-capture.js
export const rawCapture = {
  url: "https://example.com/signup",
  title: "Sign up", lang: "en", charset: "UTF-8",
  viewport: "width=device-width, initial-scale=1", description: "Create an account",
  headings: [{ level: 1, text: "Sign up" }, { level: 3, text: "Details" }],
  landmarks: [{ role: "main", label: null, tag: "main" }],
  forms: [{ name: "signup", action: "/signup", method: "post", inputs: [
    { tag: "input", type: "email", name: "email", label: "Email", required: true, placeholder: null, pattern: null },
    { tag: "input", type: "password", name: "pw", label: null, required: true, placeholder: "Password", pattern: null }
  ] }],
  links: [{ text: "Home", href: "https://example.com/" }, { text: "", href: "#" }],
  images: [{ src: "/logo.png", alt: "Logo" }, { src: "/hero.jpg", alt: null }],
  buttons: [{ text: "Create account", disabled: false }],
  domOutline: "<main><h1>Sign up</h1></main>",
  consoleErrors: [{ level: "error", message: "Uncaught TypeError: x is undefined", time: 1 }]
};
export const networkErrors = [
  { url: "https://example.com/api/config", status: 500, method: "GET", type: "xmlhttprequest", time: 2 }
];
```

- [ ] **Step 2: Write the failing test**

```js
// tests/extract.test.js
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildContext } from "../lib/extract.js";
import { rawCapture, networkErrors } from "./fixtures/raw-capture.js";

test("passes through normalized fields and attaches network errors", () => {
  const ctx = buildContext(rawCapture, networkErrors);
  assert.equal(ctx.url, "https://example.com/signup");
  assert.equal(ctx.forms[0].inputs.length, 2);
  assert.equal(ctx.networkErrors.length, 1);
  assert.equal(ctx.truncated.domOutline, false);
});

test("caps domOutline and flags truncation", () => {
  const big = { ...rawCapture, domOutline: "x".repeat(20000) };
  const ctx = buildContext(big, [], { maxHtml: 100 });
  assert.equal(ctx.domOutline.length, 100);
  assert.equal(ctx.truncated.domOutline, true);
});

test("caps list lengths and flags truncation", () => {
  const many = { ...rawCapture, links: Array.from({ length: 500 }, (_, i) => ({ text: "l" + i, href: "/" + i })) };
  const ctx = buildContext(many, [], { maxItems: 10 });
  assert.equal(ctx.links.length, 10);
  assert.equal(ctx.truncated.links, true);
});

test("defaults missing arrays to empty", () => {
  const ctx = buildContext({ url: "u", title: "t" }, undefined);
  assert.deepEqual(ctx.links, []);
  assert.deepEqual(ctx.networkErrors, []);
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /home/aswanirs/Downloads/qa-test-extension && node --test tests/extract.test.js`
Expected: FAIL — cannot find module `../lib/extract.js`.

- [ ] **Step 4: Write `lib/extract.js`**

```js
// lib/extract.js
function capList(arr, max, flag) {
  const list = Array.isArray(arr) ? arr : [];
  if (list.length > max) { flag.value = true; return list.slice(0, max); }
  return list;
}

export function buildContext(raw = {}, networkErrors = [], opts = {}) {
  const maxHtml = opts.maxHtml ?? 12000;
  const maxItems = opts.maxItems ?? 200;
  const truncated = { domOutline: false, links: false, images: false };

  const linksFlag = { value: false };
  const imagesFlag = { value: false };
  const links = capList(raw.links, maxItems, linksFlag);
  const images = capList(raw.images, maxItems, imagesFlag);
  truncated.links = linksFlag.value;
  truncated.images = imagesFlag.value;

  let domOutline = typeof raw.domOutline === "string" ? raw.domOutline : "";
  if (domOutline.length > maxHtml) { domOutline = domOutline.slice(0, maxHtml); truncated.domOutline = true; }

  return {
    url: raw.url ?? "", title: raw.title ?? "", lang: raw.lang ?? "",
    charset: raw.charset ?? "", viewport: raw.viewport ?? null, description: raw.description ?? null,
    headings: Array.isArray(raw.headings) ? raw.headings : [],
    landmarks: Array.isArray(raw.landmarks) ? raw.landmarks : [],
    forms: Array.isArray(raw.forms) ? raw.forms : [],
    links, images,
    buttons: Array.isArray(raw.buttons) ? raw.buttons : [],
    domOutline,
    consoleErrors: Array.isArray(raw.consoleErrors) ? raw.consoleErrors : [],
    interactions: Array.isArray(raw.interactions) ? raw.interactions : [],
    networkErrors: Array.isArray(networkErrors) ? networkErrors : [],
    truncated,
  };
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/aswanirs/Downloads/qa-test-extension && node --test tests/extract.test.js`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
cd /home/aswanirs/Downloads/qa-test-extension
git add lib/extract.js tests/extract.test.js tests/fixtures/raw-capture.js
git commit -m "feat: add context extraction with size caps"
```

---

### Task 3: `qa-prompt.md` + `lib/prompt.js` — assemble the LLM message

**Files:**
- Create: `qa-prompt.md`
- Create: `lib/prompt.js`
- Test: `tests/prompt.test.js`

**Interfaces:**
- Consumes: `Context` from Task 2.
- Produces: `buildMessage(context, opts) -> { system, content }`. `opts = { mode: "snapshot"|"capture", systemPrompt: string }`. `system` is `opts.systemPrompt` with a one-line access-mode banner appended. `content` is a plain-text serialization of the context (never a huge raw blob) that names the mode and lists truncations.

- [ ] **Step 1: Write `qa-prompt.md` (adapted qa-test skill)**

Copy the body of `~/.claude/skills/qa-test/SKILL.md` (sections: role, What to test, Verify before reporting, Severity/categories, Output format 1–7, Common Mistakes) and replace the "Access method" section with:

```markdown
## Access method

You are analyzing a **captured snapshot** of a single web page (and, in capture
mode, a log of user interactions plus console/network errors observed during
them). You are NOT driving the browser yourself. Report only what the captured
context supports. Mark anything you could not directly observe as **Suspected**.
State the access mode in the Test Summary.
```

Keep the full "Output format — produce BOTH" section verbatim so the report shape is identical to the skill.

- [ ] **Step 2: Write the failing test**

```js
// tests/prompt.test.js
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildMessage } from "../lib/prompt.js";
import { rawCapture, networkErrors } from "./fixtures/raw-capture.js";
import { buildContext } from "../lib/extract.js";

const ctx = buildContext(rawCapture, networkErrors);

test("system prompt gets an access-mode banner", () => {
  const { system } = buildMessage(ctx, { mode: "snapshot", systemPrompt: "BASE" });
  assert.match(system, /^BASE/);
  assert.match(system, /Snapshot/i);
});

test("content names the mode and includes url, a form, and errors", () => {
  const { content } = buildMessage(ctx, { mode: "capture", systemPrompt: "BASE" });
  assert.match(content, /Mode: capture/);
  assert.match(content, /example\.com\/signup/);
  assert.match(content, /email/);
  assert.match(content, /500/);
  assert.match(content, /Uncaught TypeError/);
});

test("content notes truncation when present", () => {
  const t = { ...ctx, truncated: { domOutline: true, links: false, images: false } };
  const { content } = buildMessage(t, { mode: "snapshot", systemPrompt: "BASE" });
  assert.match(content, /truncated/i);
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /home/aswanirs/Downloads/qa-test-extension && node --test tests/prompt.test.js`
Expected: FAIL — cannot find module `../lib/prompt.js`.

- [ ] **Step 4: Write `lib/prompt.js`**

```js
// lib/prompt.js
function line(label, value) { return value ? `${label}: ${value}` : null; }

export function buildMessage(context, opts = {}) {
  const mode = opts.mode === "capture" ? "capture" : "snapshot";
  const modeLabel = mode === "capture" ? "Manual capture-while-you-click" : "Snapshot analysis";
  const system = `${opts.systemPrompt || ""}\n\nACCESS MODE: ${modeLabel}. Report only what this captured context supports; mark unobserved findings as Suspected.`;

  const t = context.truncated || {};
  const truncNotes = Object.entries(t).filter(([, v]) => v).map(([k]) => k);

  const parts = [
    `Mode: ${mode}`,
    line("URL", context.url),
    line("Title", context.title),
    line("Lang", context.lang),
    line("Description", context.description),
    line("Viewport", context.viewport),
    "",
    "## Heading outline",
    ...context.headings.map((h) => `${"#".repeat(h.level)} ${h.text}`),
    "",
    "## Landmarks",
    ...context.landmarks.map((l) => `- ${l.tag}${l.role ? ` role=${l.role}` : ""}${l.label ? ` label=${l.label}` : ""}`),
    "",
    "## Forms",
    ...context.forms.flatMap((f) => [
      `- form name=${f.name ?? "(none)"} action=${f.action ?? "(none)"} method=${f.method}`,
      ...f.inputs.map((i) => `    • ${i.tag}[type=${i.type}] name=${i.name ?? "(none)"} label=${i.label ?? "(none)"} required=${i.required} placeholder=${i.placeholder ?? ""} pattern=${i.pattern ?? ""}`),
    ]),
    "",
    "## Links",
    ...context.links.map((l) => `- "${l.text}" -> ${l.href}`),
    "",
    "## Images",
    ...context.images.map((im) => `- ${im.src} alt=${im.alt === null ? "(MISSING)" : `"${im.alt}"`}`),
    "",
    "## Buttons",
    ...context.buttons.map((b) => `- "${b.text}"${b.disabled ? " (disabled)" : ""}`),
    "",
    "## Console errors",
    ...context.consoleErrors.map((e) => `- [${e.level}] ${e.message}`),
    "",
    "## Network errors",
    ...context.networkErrors.map((n) => `- ${n.status} ${n.method} ${n.url}`),
  ];

  if (context.interactions && context.interactions.length) {
    parts.push("", "## Recorded interactions", ...context.interactions.map((a) => `- ${a.action} ${a.target}${a.detail ? ` (${a.detail})` : ""}`));
  }
  parts.push("", "## DOM outline", context.domOutline);
  if (truncNotes.length) parts.push("", `NOTE: the following were truncated to fit budget: ${truncNotes.join(", ")}.`);

  const content = parts.filter((p) => p !== null).join("\n");
  return { system, content };
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/aswanirs/Downloads/qa-test-extension && node --test tests/prompt.test.js`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
cd /home/aswanirs/Downloads/qa-test-extension
git add qa-prompt.md lib/prompt.js tests/prompt.test.js
git commit -m "feat: add prompt assembly and adapted qa-test system prompt"
```

---

### Task 4: `lib/providers/*` — multi-provider LLM adapters

**Files:**
- Create: `lib/providers/anthropic.js`
- Create: `lib/providers/openai.js`
- Create: `lib/providers/gemini.js`
- Create: `lib/providers/index.js`
- Test: `tests/providers.test.js`

**Interfaces:**
- Each adapter exports `buildRequest({ model, system, content, image })` → `{ url, headers, body }` (body is a JS object), `parseResponse(json)` → `string`, and `run({ apiKey, model, system, content, image, fetchImpl })` → `Promise<{ text }>`. `image`, when present, is a `{ mediaType, dataBase64 }` object. `fetchImpl` defaults to global `fetch`.
- `index.js` exports `getProvider(name)` → the adapter object, throwing on unknown name.

- [ ] **Step 1: Write the failing test**

```js
// tests/providers.test.js
import { test } from "node:test";
import assert from "node:assert/strict";
import { getProvider } from "../lib/providers/index.js";

const args = { apiKey: "k", model: "m", system: "S", content: "C" };

test("getProvider throws on unknown", () => {
  assert.throws(() => getProvider("nope"), /unknown provider/i);
});

test("anthropic builds request with browser-access header and parses text", () => {
  const p = getProvider("anthropic");
  const req = p.buildRequest(args);
  assert.equal(req.url, "https://api.anthropic.com/v1/messages");
  assert.equal(req.headers["anthropic-dangerous-direct-browser-access"], "true");
  assert.equal(req.headers["x-api-key"], undefined); // key added in run(), not buildRequest
  assert.equal(p.parseResponse({ content: [{ type: "text", text: "hi" }] }), "hi");
});

test("openai builds chat request and parses text", () => {
  const p = getProvider("openai");
  const req = p.buildRequest(args);
  assert.equal(req.url, "https://api.openai.com/v1/chat/completions");
  assert.equal(req.body.messages[0].role, "system");
  assert.equal(p.parseResponse({ choices: [{ message: { content: "yo" } }] }), "yo");
});

test("gemini builds request and parses text", () => {
  const p = getProvider("gemini");
  const req = p.buildRequest({ ...args, model: "gemini-2.0-flash" });
  assert.match(req.url, /generativelanguage\.googleapis\.com/);
  assert.equal(p.parseResponse({ candidates: [{ content: { parts: [{ text: "ok" }] } }] }), "ok");
});

test("run() calls fetchImpl and returns parsed text", async () => {
  const p = getProvider("openai");
  const fetchImpl = async () => ({ ok: true, json: async () => ({ choices: [{ message: { content: "done" } }] }) });
  const out = await p.run({ ...args, fetchImpl });
  assert.equal(out.text, "done");
});

test("run() throws provider error message on non-ok", async () => {
  const p = getProvider("anthropic");
  const fetchImpl = async () => ({ ok: false, status: 401, text: async () => "bad key" });
  await assert.rejects(() => p.run({ ...args, fetchImpl }), /401.*bad key/);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/aswanirs/Downloads/qa-test-extension && node --test tests/providers.test.js`
Expected: FAIL — cannot find module `../lib/providers/index.js`.

- [ ] **Step 3: Write `lib/providers/anthropic.js`**

```js
// lib/providers/anthropic.js
export function buildRequest({ model, system, content, image }) {
  const blocks = [{ type: "text", text: content }];
  if (image) blocks.push({ type: "image", source: { type: "base64", media_type: image.mediaType, data: image.dataBase64 } });
  return {
    url: "https://api.anthropic.com/v1/messages",
    headers: {
      "content-type": "application/json",
      "anthropic-version": "2023-06-01",
      "anthropic-dangerous-direct-browser-access": "true",
    },
    body: { model, max_tokens: 4096, system, messages: [{ role: "user", content: blocks }] },
  };
}
export function parseResponse(json) {
  return (json.content || []).filter((b) => b.type === "text").map((b) => b.text).join("");
}
export async function run({ apiKey, model, system, content, image, fetchImpl = fetch }) {
  const req = buildRequest({ model, system, content, image });
  const res = await fetchImpl(req.url, { method: "POST", headers: { ...req.headers, "x-api-key": apiKey }, body: JSON.stringify(req.body) });
  if (!res.ok) throw new Error(`Anthropic error ${res.status}: ${await res.text()}`);
  return { text: parseResponse(await res.json()) };
}
```

- [ ] **Step 4: Write `lib/providers/openai.js`**

```js
// lib/providers/openai.js
export function buildRequest({ model, system, content, image }) {
  const userContent = image
    ? [{ type: "text", text: content }, { type: "image_url", image_url: { url: `data:${image.mediaType};base64,${image.dataBase64}` } }]
    : content;
  return {
    url: "https://api.openai.com/v1/chat/completions",
    headers: { "content-type": "application/json" },
    body: { model, max_tokens: 4096, messages: [{ role: "system", content: system }, { role: "user", content: userContent }] },
  };
}
export function parseResponse(json) {
  return json.choices?.[0]?.message?.content ?? "";
}
export async function run({ apiKey, model, system, content, image, fetchImpl = fetch }) {
  const req = buildRequest({ model, system, content, image });
  const res = await fetchImpl(req.url, { method: "POST", headers: { ...req.headers, authorization: `Bearer ${apiKey}` }, body: JSON.stringify(req.body) });
  if (!res.ok) throw new Error(`OpenAI error ${res.status}: ${await res.text()}`);
  return { text: parseResponse(await res.json()) };
}
```

- [ ] **Step 5: Write `lib/providers/gemini.js`**

```js
// lib/providers/gemini.js
export function buildRequest({ model, system, content, image }) {
  const parts = [{ text: content }];
  if (image) parts.push({ inline_data: { mime_type: image.mediaType, data: image.dataBase64 } });
  return {
    url: `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`,
    headers: { "content-type": "application/json" },
    body: { system_instruction: { parts: [{ text: system }] }, contents: [{ role: "user", parts }] },
  };
}
export function parseResponse(json) {
  return json.candidates?.[0]?.content?.parts?.map((p) => p.text).join("") ?? "";
}
export async function run({ apiKey, model, system, content, image, fetchImpl = fetch }) {
  const req = buildRequest({ model, system, content, image });
  const res = await fetchImpl(`${req.url}?key=${apiKey}`, { method: "POST", headers: req.headers, body: JSON.stringify(req.body) });
  if (!res.ok) throw new Error(`Gemini error ${res.status}: ${await res.text()}`);
  return { text: parseResponse(await res.json()) };
}
```

- [ ] **Step 6: Write `lib/providers/index.js`**

```js
// lib/providers/index.js
import * as anthropic from "./anthropic.js";
import * as openai from "./openai.js";
import * as gemini from "./gemini.js";

const REGISTRY = { anthropic, openai, gemini };

export function getProvider(name) {
  const p = REGISTRY[name];
  if (!p) throw new Error(`Unknown provider: ${name}`);
  return p;
}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd /home/aswanirs/Downloads/qa-test-extension && node --test tests/providers.test.js`
Expected: PASS (6 tests).

- [ ] **Step 8: Commit**

```bash
cd /home/aswanirs/Downloads/qa-test-extension
git add lib/providers tests/providers.test.js
git commit -m "feat: add anthropic/openai/gemini provider adapters"
```

---

### Task 5: `lib/markdown.js` — render the report safely

**Files:**
- Create: `lib/markdown.js`
- Test: `tests/markdown.test.js`

**Interfaces:**
- Produces: `renderMarkdown(md) -> htmlString`. Supports headings (`#`–`######`), GitHub tables, bold `**x**`, inline code, unordered lists, and paragraphs. **HTML-escapes all text before applying formatting** (the report is untrusted LLM output rendered in the panel).

- [ ] **Step 1: Write the failing test**

```js
// tests/markdown.test.js
import { test } from "node:test";
import assert from "node:assert/strict";
import { renderMarkdown } from "../lib/markdown.js";

test("escapes html in text", () => {
  assert.match(renderMarkdown("<script>alert(1)</script>"), /&lt;script&gt;/);
  assert.doesNotMatch(renderMarkdown("<script>x</script>"), /<script>/);
});

test("renders headings and bold", () => {
  assert.match(renderMarkdown("# Title"), /<h1>Title<\/h1>/);
  assert.match(renderMarkdown("**bold**"), /<strong>bold<\/strong>/);
});

test("renders a pipe table as an html table", () => {
  const md = "| A | B |\n| --- | --- |\n| 1 | 2 |";
  const html = renderMarkdown(md);
  assert.match(html, /<table>/);
  assert.match(html, /<th>A<\/th>/);
  assert.match(html, /<td>1<\/td>/);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/aswanirs/Downloads/qa-test-extension && node --test tests/markdown.test.js`
Expected: FAIL — cannot find module `../lib/markdown.js`.

- [ ] **Step 3: Write `lib/markdown.js`**

```js
// lib/markdown.js
function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function inline(s) {
  return esc(s)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}
function isTableSep(line) { return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(line); }
function cells(line) { return line.replace(/^\s*\||\|\s*$/g, "").split("|").map((c) => c.trim()); }

export function renderMarkdown(md) {
  const lines = String(md).split("\n");
  const out = [];
  let i = 0, list = false;
  const closeList = () => { if (list) { out.push("</ul>"); list = false; } };

  while (i < lines.length) {
    const line = lines[i];
    // table
    if (line.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      closeList();
      const header = cells(line);
      out.push("<table><thead><tr>" + header.map((h) => `<th>${inline(h)}</th>`).join("") + "</tr></thead><tbody>");
      i += 2;
      while (i < lines.length && lines[i].includes("|")) {
        out.push("<tr>" + cells(lines[i]).map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>");
        i++;
      }
      out.push("</tbody></table>");
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) { closeList(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); i++; continue; }
    const li = line.match(/^\s*[-*]\s+(.*)$/);
    if (li) { if (!list) { out.push("<ul>"); list = true; } out.push(`<li>${inline(li[1])}</li>`); i++; continue; }
    if (line.trim() === "") { closeList(); i++; continue; }
    closeList(); out.push(`<p>${inline(line)}</p>`); i++;
  }
  closeList();
  return out.join("\n");
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/aswanirs/Downloads/qa-test-extension && node --test tests/markdown.test.js`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
cd /home/aswanirs/Downloads/qa-test-extension
git add lib/markdown.js tests/markdown.test.js
git commit -m "feat: add safe markdown renderer for the report"
```

---

### Task 6: Content capture — `content/capture.js` + `content/error-hooks.js`

**Files:**
- Create: `content/error-hooks.js`
- Create: `content/capture.js`

**Interfaces:**
- Produces: when executed in a tab, `capture.js` returns a `RawCapture` object (minus `networkErrors`, which the background adds). `error-hooks.js` runs in the MAIN world and stores errors on `window.__qaErrors`.

- [ ] **Step 1: Write `content/error-hooks.js` (MAIN world)**

```js
// Runs in the page's MAIN world. Buffers console + runtime errors.
(function () {
  if (window.__qaHooksInstalled) return;
  window.__qaHooksInstalled = true;
  window.__qaErrors = [];
  const push = (level, message) => window.__qaErrors.push({ level, message: String(message), time: Date.now() });
  const origErr = console.error, origWarn = console.warn;
  console.error = function (...a) { push("error", a.join(" ")); return origErr.apply(this, a); };
  console.warn = function (...a) { push("warn", a.join(" ")); return origWarn.apply(this, a); };
  window.addEventListener("error", (e) => push("error", e.message || (e.error && e.error.message) || "error"));
  window.addEventListener("unhandledrejection", (e) => push("error", "Unhandled rejection: " + (e.reason && e.reason.message ? e.reason.message : e.reason)));
})();
```

- [ ] **Step 2: Write `content/capture.js` (ISOLATED world, returns RawCapture)**

```js
// Reads the live DOM and returns a RawCapture. Executed via chrome.scripting.
(function () {
  const text = (el) => (el.textContent || "").trim().slice(0, 200);
  const labelFor = (input) => {
    if (input.id) { const l = document.querySelector(`label[for="${CSS.escape(input.id)}"]`); if (l) return text(l); }
    const wrap = input.closest("label"); if (wrap) return text(wrap);
    return input.getAttribute("aria-label") || null;
  };
  const headings = [...document.querySelectorAll("h1,h2,h3,h4,h5,h6")].map((h) => ({ level: +h.tagName[1], text: text(h) }));
  const landmarks = [...document.querySelectorAll("main,nav,header,footer,aside,[role]")].slice(0, 100).map((el) => ({
    role: el.getAttribute("role") || "", label: el.getAttribute("aria-label") || null, tag: el.tagName.toLowerCase(),
  }));
  const forms = [...document.querySelectorAll("form")].map((f) => ({
    name: f.getAttribute("name"), action: f.getAttribute("action"), method: (f.getAttribute("method") || "get").toLowerCase(),
    inputs: [...f.querySelectorAll("input,select,textarea")].map((i) => ({
      tag: i.tagName.toLowerCase(), type: i.getAttribute("type") || i.tagName.toLowerCase(), name: i.getAttribute("name"),
      label: labelFor(i), required: i.hasAttribute("required"), placeholder: i.getAttribute("placeholder"), pattern: i.getAttribute("pattern"),
    })),
  }));
  const links = [...document.querySelectorAll("a")].map((a) => ({ text: text(a), href: a.getAttribute("href") || "" }));
  const images = [...document.querySelectorAll("img")].map((im) => ({ src: im.getAttribute("src") || "", alt: im.hasAttribute("alt") ? im.getAttribute("alt") : null }));
  const buttons = [...document.querySelectorAll("button,[role=button],input[type=submit]")].map((b) => ({ text: text(b) || b.value || "", disabled: b.disabled === true }));

  return {
    url: location.href, title: document.title, lang: document.documentElement.lang || "",
    charset: document.characterSet || "", viewport: (document.querySelector('meta[name=viewport]') || {}).content || null,
    description: (document.querySelector('meta[name=description]') || {}).content || null,
    headings, landmarks, forms, links, images, buttons,
    domOutline: document.body ? document.body.outerHTML : "",
    consoleErrors: Array.isArray(window.__qaErrors) ? window.__qaErrors.slice(-200) : [],
  };
})();
```

- [ ] **Step 3: Syntax check**

Run: `cd /home/aswanirs/Downloads/qa-test-extension && node --check content/capture.js && node --check content/error-hooks.js`
Expected: no output (syntax OK). Full DOM capture is exercised in the Task 8 end-to-end run.

- [ ] **Step 4: Commit**

```bash
cd /home/aswanirs/Downloads/qa-test-extension
git add content/capture.js content/error-hooks.js
git commit -m "feat: add DOM capture and console error hooks"
```

---

### Task 7: Background orchestration + network capture

**Files:**
- Modify: `background.js`

**Interfaces:**
- Consumes: `content/error-hooks.js`, `content/capture.js`, `lib/extract.js`, `lib/prompt.js`, `lib/providers/index.js`, `qa-prompt.md`.
- Produces: handles `chrome.runtime.onMessage` `{ type: "runQa", tabId, mode }` → resolves `{ ok: true, report }` or `{ ok: false, error }`. Records network errors per tab via `chrome.webRequest`.

- [ ] **Step 1: Rewrite `background.js`**

```js
import { buildContext } from "./lib/extract.js";
import { buildMessage } from "./lib/prompt.js";
import { getProvider } from "./lib/providers/index.js";

// --- side panel open ---
chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});

// --- network error capture per tab ---
const netErrors = new Map(); // tabId -> NetworkError[]
function record(tabId, e) {
  if (tabId < 0) return;
  const list = netErrors.get(tabId) || [];
  list.push(e); if (list.length > 200) list.shift();
  netErrors.set(tabId, list);
}
chrome.webRequest.onCompleted.addListener((d) => {
  if (d.statusCode >= 400) record(d.tabId, { url: d.url, status: d.statusCode, method: d.method, type: d.type, time: d.timeStamp });
}, { urls: ["<all_urls>"] });
chrome.webRequest.onErrorOccurred.addListener((d) => {
  record(d.tabId, { url: d.url, status: 0, method: d.method, type: d.type, time: d.timeStamp });
}, { urls: ["<all_urls>"] });
chrome.tabs.onRemoved.addListener((tabId) => netErrors.delete(tabId));

async function loadSystemPrompt() {
  const res = await fetch(chrome.runtime.getURL("qa-prompt.md"));
  return res.text();
}
async function getSettings() {
  const { settings } = await chrome.storage.local.get("settings");
  return settings || {};
}

async function runQa(tabId, mode) {
  const settings = await getSettings();
  const provider = settings.provider;
  const apiKey = settings.apiKeys && settings.apiKeys[provider];
  const model = settings.models && settings.models[provider];
  if (!provider || !apiKey || !model) throw new Error("Set a provider, model, and API key in settings first.");

  // Install error hooks (MAIN world), then capture (ISOLATED world).
  await chrome.scripting.executeScript({ target: { tabId }, files: ["content/error-hooks.js"], world: "MAIN" }).catch(() => {});
  const [{ result: raw }] = await chrome.scripting.executeScript({ target: { tabId }, files: ["content/capture.js"] });

  const context = buildContext(raw, netErrors.get(tabId) || []);
  const systemPrompt = await loadSystemPrompt();
  const { system, content } = buildMessage(context, { mode, systemPrompt });

  const { text } = await getProvider(provider).run({ apiKey, model, system, content });
  return text;
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "runQa") {
    runQa(msg.tabId, msg.mode).then((report) => sendResponse({ ok: true, report }))
      .catch((e) => sendResponse({ ok: false, error: String(e.message || e) }));
    return true; // async
  }
});
```

- [ ] **Step 2: Syntax check**

Run: `cd /home/aswanirs/Downloads/qa-test-extension && node --check background.js`
Expected: no output.
Note: `node --check` validates syntax only; `chrome.*` and ESM `import` in the service worker are exercised at load time in Chrome (Task 8 Step 6). The `manifest.json` already declares `"type": "module"` for the background (Task 1).

- [ ] **Step 3: Commit**

```bash
cd /home/aswanirs/Downloads/qa-test-extension
git add background.js
git commit -m "feat: orchestrate capture, prompt, and provider call in background"
```

---

### Task 8: Side panel UI — settings, run, capture mode, report, export

**Files:**
- Modify: `sidepanel/sidepanel.html`
- Modify: `sidepanel/sidepanel.js`
- Modify: `sidepanel/sidepanel.css`
- Create: `content/recorder.js`
- Modify: `content/capture.js`

**Interfaces:**
- Consumes: `background` `runQa` message; `lib/markdown.js` (`renderMarkdown`).
- Produces: full working UI. `recorder.js` toggles interaction logging by storing entries on `window.__qaInteractions`, read by `capture.js`.

- [ ] **Step 1: Write `content/recorder.js`**

```js
// Toggles capture-while-you-click. Stores interactions on window.__qaInteractions.
(function () {
  const on = !window.__qaRecording;
  window.__qaRecording = on;
  if (on) {
    window.__qaInteractions = [];
    const desc = (el) => el ? `${el.tagName?.toLowerCase() || "?"}${el.id ? "#" + el.id : ""}${el.name ? `[name=${el.name}]` : ""}` : "?";
    const log = (action, e) => window.__qaInteractions.push({ action, target: desc(e.target), detail: (e.target && (e.target.value ?? e.target.textContent || "").toString().slice(0, 40)) || null, time: Date.now() });
    window.__qaListeners = { click: (e) => log("click", e), input: (e) => log("input", e), submit: (e) => log("submit", e) };
    for (const [k, fn] of Object.entries(window.__qaListeners)) document.addEventListener(k, fn, true);
  } else if (window.__qaListeners) {
    for (const [k, fn] of Object.entries(window.__qaListeners)) document.removeEventListener(k, fn, true);
  }
  return { recording: on, count: (window.__qaInteractions || []).length };
})();
```

- [ ] **Step 2: Merge interactions into capture**

In `content/capture.js`, add this line to the returned object, immediately before the `domOutline:` line:
```js
    interactions: Array.isArray(window.__qaInteractions) ? window.__qaInteractions.slice(-200) : [],
```

- [ ] **Step 3: Write `sidepanel/sidepanel.html`**

```html
<!doctype html>
<html>
<head><meta charset="utf-8" /><link rel="stylesheet" href="sidepanel.css" /></head>
<body>
  <header>
    <h1>QA Test</h1>
    <button id="toggleSettings" class="link">Settings</button>
  </header>

  <section id="settings" hidden>
    <label>Provider
      <select id="provider">
        <option value="anthropic">Anthropic</option>
        <option value="openai">OpenAI</option>
        <option value="gemini">Gemini</option>
      </select>
    </label>
    <label>Model <input id="model" type="text" placeholder="e.g. claude-sonnet-5" /></label>
    <label>API key <input id="apiKey" type="password" placeholder="sk-..." /></label>
    <div class="row">
      <button id="saveSettings">Save</button>
      <button id="clearKeys" class="danger">Clear keys</button>
    </div>
    <p class="note">Keys are stored locally in your browser and sent only to the selected provider.</p>
  </section>

  <section id="run">
    <label><input type="radio" name="mode" value="snapshot" checked /> Snapshot</label>
    <label><input type="radio" name="mode" value="capture" /> Capture-while-you-click</label>
    <div class="row">
      <button id="record" hidden>Start recording</button>
      <button id="runQa">Run QA test</button>
    </div>
  </section>

  <p id="status"></p>
  <article id="report"></article>
  <div class="row" id="exportRow" hidden>
    <button id="copy">Copy</button>
    <button id="download">Download .md</button>
  </div>

  <script type="module" src="sidepanel.js"></script>
</body>
</html>
```

- [ ] **Step 4: Write `sidepanel/sidepanel.js`**

```js
import { renderMarkdown } from "../lib/markdown.js";

const $ = (id) => document.getElementById(id);
let lastReport = "";

async function activeTabId() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab && tab.id;
}
function mode() { return document.querySelector('input[name=mode]:checked').value; }
function setStatus(s) { $("status").textContent = s; }

// --- settings ---
async function loadSettings() {
  const { settings } = await chrome.storage.local.get("settings");
  const s = settings || { provider: "anthropic", models: {}, apiKeys: {} };
  $("provider").value = s.provider || "anthropic";
  $("model").value = (s.models && s.models[$("provider").value]) || "";
  $("apiKey").value = (s.apiKeys && s.apiKeys[$("provider").value]) || "";
  return s;
}
$("provider").addEventListener("change", loadSettings);
$("toggleSettings").addEventListener("click", () => { $("settings").hidden = !$("settings").hidden; });
$("saveSettings").addEventListener("click", async () => {
  const { settings } = await chrome.storage.local.get("settings");
  const s = settings || { models: {}, apiKeys: {} };
  const p = $("provider").value;
  s.provider = p; s.models = s.models || {}; s.apiKeys = s.apiKeys || {};
  s.models[p] = $("model").value.trim(); s.apiKeys[p] = $("apiKey").value.trim();
  await chrome.storage.local.set({ settings: s });
  setStatus("Settings saved.");
});
$("clearKeys").addEventListener("click", async () => {
  await chrome.storage.local.set({ settings: { provider: $("provider").value, models: {}, apiKeys: {} } });
  $("apiKey").value = ""; setStatus("Keys cleared.");
});

// --- capture mode toggle ---
document.querySelectorAll('input[name=mode]').forEach((r) => r.addEventListener("change", () => {
  $("record").hidden = mode() !== "capture";
}));
let recording = false;
$("record").addEventListener("click", async () => {
  const tabId = await activeTabId();
  if (!tabId) return setStatus("No active tab.");
  const [{ result }] = await chrome.scripting.executeScript({ target: { tabId }, files: ["content/recorder.js"] });
  recording = result.recording;
  $("record").textContent = recording ? "Stop recording" : "Start recording";
  setStatus(recording ? "Recording — interact with the page, then Run QA test." : `Recorded ${result.count} interactions.`);
});

// --- run ---
$("runQa").addEventListener("click", async () => {
  const tabId = await activeTabId();
  if (!tabId) return setStatus("No active tab.");
  setStatus("Capturing and analyzing…");
  $("report").innerHTML = ""; $("exportRow").hidden = true;
  const resp = await chrome.runtime.sendMessage({ type: "runQa", tabId, mode: mode() });
  if (!resp) return setStatus("No response (is the page a restricted chrome:// page?).");
  if (!resp.ok) return setStatus("Error: " + resp.error);
  lastReport = resp.report;
  $("report").innerHTML = renderMarkdown(resp.report);
  $("exportRow").hidden = false;
  setStatus("Done.");
});

// --- export ---
$("copy").addEventListener("click", () => navigator.clipboard.writeText(lastReport).then(() => setStatus("Copied.")));
$("download").addEventListener("click", () => {
  const blob = new Blob([lastReport], { type: "text/markdown" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "qa-report.md"; a.click();
  URL.revokeObjectURL(a.href);
});

loadSettings();
```

- [ ] **Step 5: Write `sidepanel/sidepanel.css`**

```css
body { font: 13px/1.5 system-ui, sans-serif; margin: 0; padding: 12px; color: #1a1a1a; }
header { display: flex; justify-content: space-between; align-items: center; }
header h1 { font-size: 15px; margin: 0; }
label { display: block; margin: 6px 0; }
input, select { width: 100%; box-sizing: border-box; padding: 5px; font: inherit; }
select { width: auto; }
.row { display: flex; gap: 8px; margin: 8px 0; }
button { font: inherit; padding: 6px 10px; cursor: pointer; border: 1px solid #ccc; border-radius: 4px; background: #f5f5f5; }
button.link { border: none; background: none; color: #06c; padding: 0; }
button.danger { color: #b00; }
#run label { display: inline-block; margin-right: 12px; }
.note { color: #666; font-size: 11px; }
#status { color: #444; font-style: italic; }
#report { font-size: 12px; }
#report table { border-collapse: collapse; width: 100%; overflow-x: auto; display: block; }
#report th, #report td { border: 1px solid #ddd; padding: 4px 6px; text-align: left; vertical-align: top; }
#report h1 { font-size: 15px; } #report h2 { font-size: 14px; } #report h3 { font-size: 13px; }
```

- [ ] **Step 6: Syntax check + reload**

Run: `cd /home/aswanirs/Downloads/qa-test-extension && node --check content/recorder.js && node --check content/capture.js`
Expected: no output.
Then in `chrome://extensions`, click reload on the extension.

- [ ] **Step 7: End-to-end manual acceptance**

Set up: open Settings, choose a provider, paste a valid key + model, Save.
1. **Snapshot on a form page** (e.g. a signup page). Select Snapshot → Run QA test.
   Expected: status "Capturing and analyzing…" → "Done."; report renders with a Test Summary stating Snapshot mode and a severity-grouped bug table.
2. **Console/network errors surfaced.** Open a page you know logs a console error or has a failing request; Run QA test.
   Expected: report references the console/network error.
3. **Capture mode.** Select Capture-while-you-click → Start recording → click/type on the page → Run QA test.
   Expected: report's Test Summary states Manual capture mode and references your interactions.
4. **Restricted page.** Navigate to `chrome://settings`, Run QA test.
   Expected: friendly status message, no crash.
5. **No key.** Clear keys, Run QA test.
   Expected: status "Error: Set a provider, model, and API key in settings first."
6. **Export.** After a report, click Copy and Download .md.
   Expected: clipboard has the markdown; a `qa-report.md` downloads.

- [ ] **Step 8: Commit**

```bash
cd /home/aswanirs/Downloads/qa-test-extension
git add sidepanel content/recorder.js content/capture.js
git commit -m "feat: side panel UI with settings, capture mode, report, export"
```

---

### Task 9: README + full test run

**Files:**
- Create: `README.md`

**Interfaces:** none (documentation + verification).

- [ ] **Step 1: Write `README.md`**

Include: what it is (generic MV3 QA-testing extension, its own standalone repo, **no** connection to any backend); install (Load unpacked → select the repo root); first-run setup (Settings → provider/model/key); the two modes (snapshot / capture-while-you-click); where keys are stored (local, provider-only); restricted-page limitation; supported providers with example model names (`anthropic` → `claude-sonnet-5`, `openai` → `gpt-...`, `gemini` → `gemini-2.0-flash`); and the dev test command `node --test tests/`.

- [ ] **Step 2: Run the full unit suite**

Run: `cd /home/aswanirs/Downloads/qa-test-extension && node --test tests/`
Expected: all tests pass (extract, prompt, providers, markdown).

- [ ] **Step 3: Commit**

```bash
cd /home/aswanirs/Downloads/qa-test-extension
git add README.md
git commit -m "docs: add README for qa-test Chrome extension"
```

---

## Self-Review

**Spec coverage:**
- Standalone MV3 + side panel, own git repo → Tasks 0, 1. ✓
- Snapshot + capture-while-you-click modes → capture.js/recorder.js (Tasks 6, 8), mode through prompt (Task 3) and UI (Task 8). ✓
- Multi-provider LLM with keys in `chrome.storage.local`, calls from service worker → Tasks 4, 7, 8. ✓
- DOM/forms/links/images/a11y/meta capture → Task 6. ✓
- MAIN-world console hook → Task 6; webRequest network capture → Task 7. ✓
- Adapted qa-prompt.md preserving output sections + access-mode/Suspected rule → Task 3. ✓
- Token budgeting / truncation notes → Task 2 + surfaced in Task 3. ✓
- Report rendering (markdown, HTML table) + Copy/Download → Tasks 5, 8. ✓
- Error handling (no key, restricted page, LLM errors, oversized capture) → Tasks 2, 7, 8. ✓
- Pure-module unit tests via `node --test`; extension no-build → Tasks 2–5, 9. ✓
- No AutoQA Studio coupling; own repo at `/home/aswanirs/Downloads/qa-test-extension/` → Task 0 + Global Constraints. ✓

**Placeholder scan:** No TBD/TODO; every code step has complete code. Icon placeholder bytes are called out with a real fallback instruction.

**Type consistency:** `RawCapture`/`Context`/`NetworkError` shapes match across capture.js → extract.js → prompt.js. Provider `buildRequest/parseResponse/run` signatures consistent across the three adapters and the test. `renderMarkdown`, `buildContext`, `buildMessage`, `getProvider` names used consistently.

**One intentional deviation from the spec:** the spec lists vision/screenshot attachment in v1. The provider adapters fully support an `image` argument (`{ mediaType, dataBase64 }`), but the plan does not wire `chrome.tabs.captureVisibleTab` into the run flow, to keep v1 focused. To add it: in `runQa`, capture the visible tab, base64-encode it, and pass `image` to `run()` guarded by a per-model vision flag.
