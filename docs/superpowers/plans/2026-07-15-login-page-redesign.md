# Login Page Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the overlay sign-in card with a full-page, branded, light-themed login that stacks email → password → login, offers a Sign in ⇄ Create account toggle, and surfaces auth errors inline.

**Architecture:** Frontend only. Rewrite the `#authSection` markup in `frontend/index.html` into a full-page layout (top bar + centered white card) with a scoped `<style>` block, then wire mode-toggle and inline-error behavior in `frontend/app.js`. No backend, router, or `/api/auth/*` change. Reuses the existing `/api/auth/login` and `/api/auth/register` calls and the existing token/session flow (`setToken`, `refreshProjects`, `refreshUserChip`, `handleRoute`).

**Tech Stack:** Vanilla JS (`frontend/app.js`), static HTML (`frontend/index.html`), inline + scoped `<style>` CSS. No JS test harness exists; verification is browser-driven via the running dev server (Playwright MCP or a manual browser) plus `curl` for the API.

## Global Constraints

- Frontend only — do **not** touch `backend/`, routers, or `/api/auth/*`. (spec: "No backend / API changes")
- Do **not** introduce new Tailwind utility classes — use inline styles / a scoped `<style>` block with explicit hex colors, so `frontend/styles-tailwind.css` needs no rebuild. (spec: "Styling approach")
- The login page stays **light** regardless of the app's dark/light theme — use explicit hex, not the app's `--bg-*` theme tokens. (spec: "Light & clean")
- Brand accent color is `#1856FF` (matches `<meta name="theme-color">` in `index.html`).
- Forgot-password link is an **inert placeholder** shown only after a failed login. Do not build a reset flow. (spec: "Placeholder link only")
- Do **not** add a `Co-Authored-By` trailer to commits. (CLAUDE.md)
- The dev server must run with `AUTH_DISABLED=false` and a set `JWT_SECRET` for verification (already configured in `.env`). Restart uvicorn after `.env`/backend changes only — pure frontend edits are picked up on browser reload.

---

### Task 1: Full-page login markup + scoped styles

Rewrite the `#authSection` block so the login renders as an opaque, full-page, branded, light layout. This task delivers the **static** appearance (login mode only); interactivity is Task 2.

**Files:**
- Modify: `frontend/index.html:28-42` (the entire `<!-- Auth overlay -->` section)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces (element IDs Task 2 relies on):
  - `authSection` — full-page container (kept; `id` unchanged).
  - `authTitle` — `<h2>` heading, text "Sign in".
  - `authNameField` — `<div>` wrapper around the Name input, starts with class `hidden`.
  - `authName` — `<input type="text">` for the account name.
  - `authEmail` — `<input type="email">` (kept).
  - `authPassword` — `<input type="password">` (kept).
  - `authError` — `<div>` for inline error text, starts empty + `hidden`.
  - `authForgot` — `<button type="button">` "Forgot password?", starts `hidden`.
  - `btnAuthSubmit` — full-width primary `<button>`, label "Login".
  - `authToggle` — footer `<button type="button">` link, text "Not registered yet? Sign up".
  - The old `btnLogin`, `btnRegister`, and the dev-helper `<p>` are removed.

- [ ] **Step 1: Replace the auth section markup**

In `frontend/index.html`, replace the entire block from `<!-- Auth overlay -->` through its closing `</section>` (currently lines 28-42) with:

```html
  <!-- Auth: full-page login/register -->
  <section id="authSection" class="hidden fixed inset-0 z-[90] flex-col" style="display:none;background:#f1f2f4;">
    <style>
      #authSection.lg-show { display:flex !important; }
      #authSection .lg-topbar { display:flex; align-items:center; gap:10px; padding:16px 24px; background:#ffffff; border-bottom:1px solid #e3e5e8; }
      #authSection .lg-brandmark { color:#1856FF; font-size:18px; line-height:1; }
      #authSection .lg-brandname { font-weight:700; font-size:16px; color:#12212e; letter-spacing:-0.01em; }
      #authSection .lg-body { flex:1; display:flex; align-items:center; justify-content:center; padding:24px; }
      #authSection .lg-card { width:100%; max-width:400px; background:#ffffff; border:1px solid #e3e5e8; border-radius:12px; box-shadow:0 1px 2px rgba(18,33,46,.04),0 8px 28px rgba(18,33,46,.08); padding:32px; }
      #authSection .lg-title { font-size:24px; font-weight:600; color:#12212e; margin:0 0 20px; text-align:center; }
      #authSection .lg-label { display:block; font-size:13px; color:#5b6470; margin:14px 0 6px; }
      #authSection .lg-input { width:100%; box-sizing:border-box; background:#ffffff; border:1px solid #ccd0d6; border-radius:8px; padding:10px 12px; font-size:14px; color:#12212e; outline:none; }
      #authSection .lg-input:focus { border-color:#1856FF; box-shadow:0 0 0 3px rgba(24,86,255,.15); }
      #authSection .lg-error { color:#d92b3b; font-size:13px; margin-top:12px; }
      #authSection .lg-forgot { display:block; background:none; border:0; padding:0; margin-top:8px; color:#1856FF; font-size:13px; cursor:pointer; text-decoration:underline; }
      #authSection .lg-submit { width:100%; margin-top:20px; background:#1856FF; color:#ffffff; border:0; border-radius:8px; padding:11px 16px; font-size:14px; font-weight:600; cursor:pointer; }
      #authSection .lg-submit:hover { background:#1246d6; }
      #authSection .lg-footer { text-align:center; margin-top:20px; font-size:13px; color:#5b6470; }
      #authSection .lg-toggle { background:none; border:0; padding:0; color:#1856FF; font-weight:600; cursor:pointer; }
      #authSection .lg-toggle:hover { text-decoration:underline; }
      #authSection .hidden { display:none; }
    </style>
    <div class="lg-topbar">
      <span class="lg-brandmark">&#9670;</span>
      <span class="lg-brandname">AutoQA Studio</span>
    </div>
    <div class="lg-body">
      <div class="lg-card">
        <h2 id="authTitle" class="lg-title">Sign in</h2>

        <div id="authNameField" class="hidden">
          <label class="lg-label" for="authName">Name</label>
          <input id="authName" type="text" class="lg-input" autocomplete="name" />
        </div>

        <label class="lg-label" for="authEmail">Email</label>
        <input id="authEmail" type="email" class="lg-input" autocomplete="username" />

        <label class="lg-label" for="authPassword">Password</label>
        <input id="authPassword" type="password" class="lg-input" autocomplete="current-password" />

        <div id="authError" class="lg-error hidden"></div>
        <button type="button" id="authForgot" class="lg-forgot hidden">Forgot password?</button>

        <button type="button" id="btnAuthSubmit" class="lg-submit">Login</button>

        <div class="lg-footer">
          <span id="authTogglePrompt">Not registered yet?</span>
          <button type="button" id="authToggle" class="lg-toggle">Sign up</button>
        </div>
      </div>
    </div>
  </section>
```

Notes:
- `style="display:none"` + the `.lg-show` class is how the page is shown/hidden. Task 2 toggles `.lg-show`; the legacy `hidden` Tailwind class on the container is left in the class list but is inert here because inline `display:none` and `.lg-show` control visibility. (Task 2 switches all show/hide to `.lg-show` via a `showLogin()` helper and `add/removeClass`.)
- Every visual color is explicit hex — no theme tokens, no new Tailwind classes.

- [ ] **Step 2: Verify the markup is served**

Run: `curl -s http://127.0.0.1:8080/ | grep -c 'btnAuthSubmit\|authToggle\|lg-card'`
Expected: `3` (all three strings present).

- [ ] **Step 3: Verify the full-page render in a browser**

Temporarily show the page (the container is `display:none` until Task 2 wires it). In the browser console at `http://127.0.0.1:8080`, run:
`document.getElementById('authSection').classList.add('lg-show')`
Confirm visually:
- A white top bar with a `◆ AutoQA Studio` brand appears.
- A centered white card on a light-grey field with "Sign in", Email, Password, a full-width blue "Login" button, and "Not registered yet? Sign up".
- **No app chrome (sidebar/header/content) is visible** behind it.

(With Playwright MCP: `browser_navigate` to the URL, `browser_evaluate` the classList line above, then `browser_take_screenshot`.)

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html
git commit -m "feat(login): full-page branded login markup and scoped styles"
```

---

### Task 2: Login/register behavior wiring

Wire the mode toggle, inline error handling, forgot-password reveal, Name-on-register, and full-page show/hide. This delivers the working page.

**Files:**
- Modify: `frontend/app.js` — the auth functions near lines 2980-3003 (`tryRegister`, `tryLogin`, `logout`), the listener registrations near lines 3303-3305, and the three `el("authSection")?.classList.remove("hidden")` call sites (lines ~128, ~3002, ~3467).

**Interfaces:**
- Consumes (from Task 1): element IDs `authSection`, `authTitle`, `authNameField`, `authName`, `authEmail`, `authPassword`, `authError`, `authForgot`, `btnAuthSubmit`, `authToggle`, `authTogglePrompt`.
- Produces: `showLogin()`, `setAuthMode(mode)`, `showAuthError(msg, opts)`, `clearAuthError()` — used only within `app.js`.

- [ ] **Step 1: Add auth-UI helpers**

In `frontend/app.js`, immediately **above** the existing `async function tryRegister() {` (currently line 2980), insert:

```javascript
let authMode = "login"; // "login" | "register"

function clearAuthError() {
  const err = el("authError"); const forgot = el("authForgot");
  if (err) { err.textContent = ""; err.classList.add("hidden"); }
  if (forgot) forgot.classList.add("hidden");
}

function showAuthError(msg, { forgot = false } = {}) {
  const err = el("authError");
  if (err) { err.textContent = msg; err.classList.remove("hidden"); }
  const f = el("authForgot");
  if (f) f.classList.toggle("hidden", !forgot);
}

function setAuthMode(mode) {
  authMode = mode === "register" ? "register" : "login";
  const isReg = authMode === "register";
  el("authTitle").textContent = isReg ? "Create account" : "Sign in";
  el("authNameField").classList.toggle("hidden", !isReg);
  el("btnAuthSubmit").textContent = isReg ? "Create account" : "Login";
  el("authTogglePrompt").textContent = isReg ? "Already have an account?" : "Not registered yet?";
  el("authToggle").textContent = isReg ? "Sign in" : "Sign up";
  clearAuthError();
}

function showLogin() {
  const s = el("authSection"); if (!s) return;
  s.classList.add("lg-show");
  setAuthMode("login");
}
```

- [ ] **Step 2: Update `tryRegister` and `tryLogin` to hide via `.lg-show` and show inline errors**

Replace the existing `tryRegister` and `tryLogin` function bodies (currently lines 2980-2998) with:

```javascript
async function tryRegister() {
  try {
    const res = await fetchJSON("/api/auth/register", {
      method: "POST", body: JSON.stringify({
        email: el("authEmail").value.trim(),
        password: el("authPassword").value,
        name: el("authName").value.trim() || "User",
      }),
    });
    setToken(res.access_token); el("authSection").classList.remove("lg-show");
    await refreshProjects(); await refreshUserChip(); await handleRoute().catch(() => {});
  } catch (e) {
    showAuthError(String(e.message || e).replace(/^Error:\s*/, ""), { forgot: false });
  }
}

async function tryLogin() {
  try {
    const res = await fetchJSON("/api/auth/login", {
      method: "POST", body: JSON.stringify({
        email: el("authEmail").value.trim(), password: el("authPassword").value,
      }),
    });
    setToken(res.access_token); el("authSection").classList.remove("lg-show");
    await refreshProjects(); await refreshUserChip(); await handleRoute().catch(() => {});
  } catch (e) {
    showAuthError("Incorrect email or password.", { forgot: true });
  }
}
```

- [ ] **Step 3: Update `logout` to use `showLogin`**

Replace the existing `logout` function (currently lines 3000-3003):

```javascript
function logout() {
  setToken(null); selectedTcIds.clear(); refreshUserChip();
  showLogin();
}
```

- [ ] **Step 4: Route all "show the login" call sites through `showLogin()`**

There are three occurrences of the exact string `el("authSection")?.classList.remove("hidden")` (in the fetchJSON 401 handler ~line 128, and the catch handler ~line 3467; the one in `logout` was just replaced in Step 3). Replace **every remaining** occurrence of:

`el("authSection")?.classList.remove("hidden")`

with:

`showLogin()`

- [ ] **Step 5: Replace the login/register button listeners and add toggle + field listeners**

Replace the existing listener lines (currently 3303-3305):

```javascript
el("btnRegister")?.addEventListener("click", tryRegister);
el("btnLogin")?.addEventListener("click", tryLogin);
el("btnLogout")?.addEventListener("click", logout);
```

with:

```javascript
el("btnAuthSubmit")?.addEventListener("click", () => (authMode === "register" ? tryRegister() : tryLogin()));
el("authToggle")?.addEventListener("click", () => setAuthMode(authMode === "register" ? "login" : "register"));
el("btnLogout")?.addEventListener("click", logout);
["authName", "authEmail", "authPassword"].forEach((id) =>
  el(id)?.addEventListener("input", clearAuthError));
["authEmail", "authPassword", "authName"].forEach((id) =>
  el(id)?.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); authMode === "register" ? tryRegister() : tryLogin(); }
  }));
```

- [ ] **Step 6: Verify — the page gates and toggles**

Reload `http://127.0.0.1:8080` (ensure `localStorage` has no stale token: run `localStorage.removeItem("tcg_token")` in console, then reload). Confirm:
1. The full-page login shows on load, no app chrome behind it.
2. Clicking "Sign up" switches heading to "Create account", reveals the **Name** field, button reads "Create account", footer reads "Already have an account? Sign in". Clicking "Sign in" switches back.

(Playwright MCP: `browser_navigate`, `browser_evaluate` to clear the token + reload, `browser_snapshot` to read the form, `browser_click` the toggle.)

- [ ] **Step 7: Verify — wrong password shows inline error + forgot link**

In the Sign in view, enter `aswani@trypencil.com` / `wrongpass`, click Login. Confirm:
- Inline red text "Incorrect email or password." appears **inside the card**.
- The "Forgot password?" link appears (clicking it does nothing — inert placeholder, expected).
- Editing the password field clears the error and hides the forgot link.

- [ ] **Step 8: Verify — correct login loads the app, and register persists Name**

- Enter `aswani@trypencil.com` / `changeme123`, click Login → the login page disappears and the app loads.
- Log out (header "Log out"), switch to "Create account", register a new user with Name "Test QA", a fresh email, and a password → app loads and the header user chip shows the name "Test QA". (Confirms `name` is sent; currently hardcoded to "User".)

Backend sanity (optional, no UI): 
`curl -s -X POST http://127.0.0.1:8080/api/auth/login -H "Content-Type: application/json" -d '{"email":"aswani@trypencil.com","password":"changeme123"}' -w "\n%{http_code}\n" | tail -1`
Expected: `200`.

- [ ] **Step 9: Commit**

```bash
git add frontend/app.js
git commit -m "feat(login): mode toggle, inline auth errors, name on register, full-page gate"
```

---

## Self-Review

**Spec coverage:**
- Full-page, branded, light, no chrome behind → Task 1 (markup + styles), verified Task 1 Step 3 / Task 2 Step 6.
- Email → Password → Login stack → Task 1 markup.
- Sign in ⇄ Create account toggle with Name field → Task 2 Steps 1, 5; verified Step 6.
- Inline error on failure + forgot-password reveal (inert) → Task 2 Step 2; verified Step 7.
- Name persisted on register → Task 2 Step 2; verified Step 8.
- No backend change, no CSS rebuild → Global Constraints; only `index.html` + `app.js` touched.

**Placeholder scan:** No TBD/TODO; the "Forgot password?" link is an intentionally inert placeholder per the approved spec (not a plan gap).

**Type/name consistency:** IDs produced by Task 1 (`authTitle`, `authNameField`, `authName`, `authError`, `authForgot`, `btnAuthSubmit`, `authToggle`, `authTogglePrompt`) match every reference in Task 2. Helper names (`showLogin`, `setAuthMode`, `showAuthError`, `clearAuthError`) are used consistently. Show/hide uses the `.lg-show` class uniformly (added in `showLogin`, removed on success in `tryLogin`/`tryRegister`).
