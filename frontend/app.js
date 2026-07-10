/**
 * Workspace-based UI: hash routes (#/, #/project/:id/:subView), sidebar, modals.
 * TestRail-inspired layout with Jira-backlog-style test case management.
 */

function getApiBase() {
  if (typeof window === "undefined" || !window.location) return "";
  try {
    const params = new URLSearchParams(window.location.search);
    const q = params.get("api");
    if (q && /^https?:\/\//i.test(q.trim())) return q.replace(/\/$/, "");
  } catch (_) {}
  try {
    const stored = localStorage.getItem("tcg_api_base");
    if (stored && String(stored).trim()) return String(stored).replace(/\/$/, "");
  } catch (_) {}
  if (window.location.protocol === "file:") return "http://127.0.0.1:8080";
  const devUiPorts = ["5173", "3000", "4173", "5500"];
  const port = window.location.port || "";
  if (port && devUiPorts.includes(port) && (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1")) {
    return "http://127.0.0.1:8080";
  }
  return "";
}

const API = getApiBase();
const TOKEN_KEY = "tcg_token";
const HASH_STORAGE_KEY = "tcg_last_hash";
const TEST_TYPES = ["happy","edge","negative","smoke","regression","integration","api","security","accessibility","performance","boundary","usability"];

function formatIST(isoStr) {
  if (!isoStr) return "—";
  const d = new Date(isoStr);
  if (isNaN(d)) return String(isoStr);
  return d.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: true });
}

let parsers = [];
let activeParser = null;
let multiBlocks = [];
let currentProjectId = null;
let browserSessionId = null;
let browserSessionStatus = null;
let browserSessionSteps = [];
// AI exploration mode (separate from manual recording, but shares the
// browserSession* globals once a session exists).
let bsMode = "manual"; // "manual" | "ai_explore"
let bsExploreSummary = null; // { status, pages_count, actions_count, errors_count, current_url, last_action, stop_reason, error }
let bsExplorePollTimer = null;
let lastProjectData = null;
let lastLoadedFeatures = [];
let lastLoadedCases = [];
let selectedTcIds = new Set();
let genAbortController = null;
let loadingTimer = null;
let loadingStarted = 0;
let currentSubView = null;
let lastRouteProjectId = null;
let projectsList = [];
let expandedFeatures = new Set();

const el = (id) => document.getElementById(id);

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** Chevron right = collapsed; chevron down = expanded (standard accordion pattern) */
function svgChevronRight(sizeClass = "w-4 h-4") {
  return `<svg class="${sizeClass} shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>`;
}
function svgChevronDown(sizeClass = "w-4 h-4") {
  return `<svg class="${sizeClass} shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>`;
}

/** Small colored dot reflecting the last Playwright run status on a test case row. */
function lastRunDot(status) {
  if (!status) return "";
  const color = status === "passed"
    ? "var(--status-low)"
    : status === "failed"
      ? "var(--status-high)"
      : "var(--status-med)";
  const label = `Last run: ${status}`;
  return `<span title="${label}" aria-label="${label}" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:6px;vertical-align:middle;"></span>`;
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------
function getToken() { return localStorage.getItem(TOKEN_KEY); }
function setToken(t) { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY); }
function authHeaders() { const t = getToken(); return t ? { Authorization: `Bearer ${t}` } : {}; }
function parseJwtPayload(token) {
  try { const p = token.split("."); return p.length < 2 ? null : JSON.parse(atob(p[1].replace(/-/g, "+").replace(/_/g, "/"))); } catch { return null; }
}
function formatTokenExpiry(token) {
  const p = parseJwtPayload(token);
  if (!p || p.exp == null) return "";
  if (Date.now() >= p.exp * 1000) return " (expired)";
  return ` · session until ${formatIST(new Date(p.exp * 1000).toISOString())}`;
}

// ---------------------------------------------------------------------------
// HTTP helpers
// ---------------------------------------------------------------------------
function parseResponseBody(text) { try { return JSON.parse(text); } catch { return text; } }
function formatApiError(status, parsed) {
  if (parsed == null) return `HTTP ${status}`;
  if (typeof parsed === "string") return `${parsed} (HTTP ${status})`;
  if (typeof parsed === "object" && parsed.detail != null) {
    const d = parsed.detail;
    if (typeof d === "string") return `${d} (HTTP ${status})`;
    if (Array.isArray(d)) return d.map((x) => x.msg || JSON.stringify(x)).join("; ") + ` (HTTP ${status})`;
    return JSON.stringify(d) + ` (HTTP ${status})`;
  }
  return JSON.stringify(parsed) + ` (HTTP ${status})`;
}

async function fetchJSON(path, opts = {}) {
  const { headers: optHeaders, signal, ...rest } = opts;
  const r = await fetch(API + path, {
    headers: { "Content-Type": "application/json", ...authHeaders(), ...optHeaders },
    signal, ...rest,
  });
  const text = await r.text();
  const data = parseResponseBody(text);
  if (r.status === 401) { el("authSection")?.classList.remove("hidden"); }
  if (!r.ok) throw new Error(formatApiError(r.status, data));
  return data;
}

async function downloadBlob(path, filename) {
  const r = await fetch(API + path, { headers: authHeaders() });
  if (!r.ok) { const t = await r.text(); throw new Error(formatApiError(r.status, parseResponseBody(t))); }
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Loading overlay
// ---------------------------------------------------------------------------
function showLoading(message, stages) {
  // stages (optional) = [{ at: <seconds>, msg: "..." }, ...] sorted by `at`.
  // When provided, the message swaps to the latest stage whose `at` <= elapsed.
  const ov = el("loadingOverlay");
  if (!ov) return;
  ov.classList.remove("hidden"); ov.setAttribute("aria-busy", "true");
  const msg = el("loadingMessage"); if (msg) msg.textContent = message || "Working…";
  const tim = el("loadingElapsed"); if (tim) tim.textContent = "";
  loadingStarted = Date.now();
  if (loadingTimer) clearInterval(loadingTimer);
  const sortedStages = Array.isArray(stages) && stages.length ? stages.slice().sort((a, b) => a.at - b.at) : null;
  loadingTimer = setInterval(() => {
    const elapsed = Math.floor((Date.now() - loadingStarted) / 1000);
    const t = el("loadingElapsed"); if (t) t.textContent = `${elapsed}s`;
    if (sortedStages) {
      let cur = sortedStages[0].msg;
      for (const s of sortedStages) { if (s.at <= elapsed) cur = s.msg; else break; }
      const m = el("loadingMessage"); if (m && m.textContent !== cur) m.textContent = cur;
    }
  }, 500);
}
function hideLoading() {
  const ov = el("loadingOverlay");
  if (ov) { ov.classList.add("hidden"); ov.setAttribute("aria-busy", "false"); }
  if (loadingTimer) { clearInterval(loadingTimer); loadingTimer = null; }
}

// ---------------------------------------------------------------------------
// Toast notification
// ---------------------------------------------------------------------------
function showToast(message, isError = false) {
  let container = document.getElementById("toastContainer");
  if (!container) {
    container = document.createElement("div");
    container.id = "toastContainer";
    // Top-center toast stack, positioned with inline CSS so it survives even
    // if the static Tailwind build is missing -translate-x-1/2. Pointer-events
    // disabled on the container so toasts don't block clicks beneath them.
    container.style.cssText =
      "position:fixed;top:1rem;left:50%;transform:translateX(-50%);z-index:200;" +
      "display:flex;flex-direction:column;align-items:center;gap:0.5rem;pointer-events:none;";
    document.body.appendChild(container);
  }
  const toast = document.createElement("div");
  toast.className = "rounded-lg text-sm shadow-lg transition-opacity duration-300";
  // All toast styles inline so we don't depend on Tailwind arbitrary-value
  // utilities (which require a CSS rebuild to be available).
  const baseStyle =
    "padding:0.75rem 1.25rem;min-width:280px;max-width:28rem;pointer-events:auto;" +
    "display:flex;align-items:center;justify-content:center;gap:0.5rem;";
  if (isError) {
    toast.style.cssText = baseStyle +
      "background:var(--status-high-bg);color:var(--status-high);border:1px solid var(--status-high);";
    toast.innerHTML = `<span aria-hidden="true">&#9888;</span><span class="toast-text"></span>`;
  } else {
    toast.style.cssText = baseStyle +
      "background:var(--bg-surface);color:var(--text-primary);border:1px solid var(--border-default);";
    toast.innerHTML = `<span aria-hidden="true" style="color:var(--status-low);">&#10003;</span><span class="toast-text"></span>`;
  }
  toast.querySelector(".toast-text").textContent = message;
  container.appendChild(toast);
  setTimeout(() => { toast.style.opacity = "0"; setTimeout(() => toast.remove(), 300); }, 3000);
}

// Inline field-level validation. Shows a small red message under an input
// and turns the input border red. Auto-clears the next time the user focuses
// or types in that field. Use this for "please enter X" style errors on
// specific form fields; use showToast(...) for app-state errors that aren't
// tied to a single field.
function fieldError(inputEl, message) {
  if (!inputEl) return;
  clearFieldError(inputEl);
  inputEl.dataset.fieldError = "1";
  inputEl.style.borderColor = "var(--status-high)";
  const parent = inputEl.parentElement;
  if (parent) {
    const msg = document.createElement("p");
    msg.className = "field-error text-xs mt-1";
    msg.style.color = "var(--status-high)";
    msg.textContent = message;
    parent.appendChild(msg);
  }
  inputEl.focus();
  const clearOnce = () => {
    clearFieldError(inputEl);
    inputEl.removeEventListener("input", clearOnce);
    inputEl.removeEventListener("focus", clearOnce);
  };
  inputEl.addEventListener("input", clearOnce);
  inputEl.addEventListener("focus", clearOnce);
}

function clearFieldError(inputEl) {
  if (!inputEl) return;
  delete inputEl.dataset.fieldError;
  inputEl.style.borderColor = "";
  const parent = inputEl.parentElement;
  if (parent) parent.querySelectorAll(".field-error").forEach(n => n.remove());
}

// Page-centered replacement for window.confirm(). Returns a Promise<boolean>.
// opts: { title, okLabel, cancelLabel, destructive }
//   - title (optional): short heading above the message
//   - destructive: uses --status-high for the confirm button instead of --accent
function customConfirm(message, opts = {}) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "fixed inset-0 flex items-center justify-center z-[300] px-4";
    overlay.style.background = "var(--bg-overlay)";
    const okBg = opts.destructive ? "var(--status-high)" : "var(--accent)";
    const okColor = opts.destructive ? "#ffffff" : "var(--text-on-accent)";
    const titleHtml = opts.title
      ? `<h3 class="text-base font-semibold mb-2" style="color:var(--text-primary);"></h3>`
      : "";
    overlay.innerHTML = `
      <div role="alertdialog" aria-modal="true" class="rounded-xl p-6 max-w-md w-full shadow-2xl"
           style="background:var(--bg-surface);border:1px solid var(--border-default);">
        ${titleHtml}
        <p class="text-sm mb-6" style="color:var(--text-secondary);line-height:1.6;"></p>
        <div class="flex justify-end gap-3 items-center">
          <button type="button" class="cc-cancel px-4 py-2 rounded text-sm font-medium" style="background:var(--btn-neutral);color:var(--btn-neutral-text);"></button>
          <button type="button" class="cc-ok px-4 py-2 rounded text-sm font-medium" style="background:${okBg};color:${okColor};"></button>
        </div>
      </div>`;
    if (opts.title) overlay.querySelector("h3").textContent = opts.title;
    overlay.querySelector("p").textContent = message;
    overlay.querySelector(".cc-cancel").textContent = opts.cancelLabel || "Cancel";
    overlay.querySelector(".cc-ok").textContent = opts.okLabel || "Confirm";

    const onKey = (e) => {
      if (e.key === "Escape") { e.preventDefault(); close(false); }
      else if (e.key === "Enter") { e.preventDefault(); close(true); }
    };
    function close(result) {
      document.removeEventListener("keydown", onKey);
      overlay.remove();
      resolve(result);
    }
    overlay.querySelector(".cc-ok").addEventListener("click", () => close(true));
    overlay.querySelector(".cc-cancel").addEventListener("click", () => close(false));
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(false); });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(overlay);
    overlay.querySelector(".cc-ok").focus();
  });
}

// ---------------------------------------------------------------------------
// Routing
// ---------------------------------------------------------------------------
function navigateTo(hash) { if (!hash.startsWith("#")) hash = "#" + hash; window.location.hash = hash; }
function parseRoute() {
  let raw = window.location.hash.replace(/^#/, "") || "/";
  if (raw === "" || raw === "/") return { mode: "home" };
  // Feature details: #/project/:projectId/feature/:featureId
  const fm = raw.match(/^\/project\/([^/]+)\/feature\/([^/]+)\/?$/);
  if (fm) {
    return { mode: "feature", projectId: fm[1], featureId: fm[2] };
  }
  const m = raw.match(/^\/project\/([^/]+)(?:\/([^/]+))?$/);
  if (m) {
    const sub = m[2] || "overview";
    const allowed = ["overview", "tests", "dashboard", "settings"];
    return { mode: "workspace", projectId: m[1], subView: allowed.includes(sub) ? sub : "overview" };
  }
  return { mode: "home" };
}
function persistHash() { try { localStorage.setItem(HASH_STORAGE_KEY, window.location.hash || "#/"); } catch (_) {} }
function showShell(which) {
  el("viewProjectList")?.classList.toggle("hidden", which !== "home");
  el("viewWorkspace")?.classList.toggle("hidden", which !== "workspace");
  el("projectSwitcherWrap")?.classList.toggle("hidden", which !== "workspace");
  // Drives visibility of the mobile hamburger (see styles.css).
  document.body.classList.toggle("in-workspace", which === "workspace");
  if (which !== "workspace") closeSidebarMobile();
}
function updateSidebarLinks(projectId) {
  document.querySelectorAll("#sidebarNav a[data-sub]").forEach((a) => { a.href = `#/project/${projectId}/${a.getAttribute("data-sub")}`; });
}
function updateSidebarActive(sub) {
  document.querySelectorAll("#sidebarNav a[data-sub]").forEach((a) => {
    const on = a.getAttribute("data-sub") === sub;
    if (on) {
      a.style.background = "var(--accent-subtle)";
      a.style.borderLeft = "2px solid var(--accent)";
      a.style.color = "var(--accent-text)";
    } else {
      a.style.background = "";
      a.style.borderLeft = "";
      a.style.color = "var(--text-secondary)";
    }
  });
}
function showWorkspaceSection(sub) {
  const map = {
    overview: "viewOverview",
    tests: "viewTests",
    dashboard: "viewDashboard",
    settings: "viewSettings",
    feature: "viewFeatureDetail",
  };
  Object.entries(map).forEach(([key, id]) => { el(id)?.classList.toggle("hidden", key !== sub); });
  // The bulk-actions bar lives in viewTests; hide it on every other section.
  if (sub !== "tests") el("bulkActionsBar")?.classList.add("hidden");
  else if (typeof updateBulkBar === "function") updateBulkBar();
}
function openSidebarMobile() {
  el("sidebar")?.classList.add("sidebar-open");
  el("sidebarBackdrop")?.classList.add("sidebar-open");
}
function closeSidebarMobile() {
  el("sidebar")?.classList.remove("sidebar-open");
  el("sidebarBackdrop")?.classList.remove("sidebar-open");
}

async function handleRoute() {
  const route = parseRoute();
  persistHash();
  currentSubView = route.mode === "workspace" ? route.subView : null;
  if (route.mode === "home") {
    lastRouteProjectId = null; currentProjectId = null;
    showShell("home"); closeSidebarMobile();
    try { await refreshProjects(); } catch (_) {}
    return;
  }
  if (route.mode === "feature") {
    showShell("workspace");
    updateSidebarLinks(route.projectId); updateSidebarActive("tests");
    showWorkspaceSection("feature");
    currentProjectId = route.projectId;
    updateProjectSwitcherLabel();
    if (lastRouteProjectId !== route.projectId) {
      lastRouteProjectId = route.projectId;
      await loadProjectWorkspaceData();
    }
    await renderFeatureDetailPage(route.projectId, route.featureId);
    closeSidebarMobile();
    return;
  }
  const { projectId, subView } = route;
  showShell("workspace");
  updateSidebarLinks(projectId); updateSidebarActive(subView); showWorkspaceSection(subView);
  currentProjectId = projectId;
  updateProjectSwitcherLabel();
  if (lastRouteProjectId !== projectId) {
    lastRouteProjectId = projectId;
    await loadProjectWorkspaceData();
  }
  if (subView === "tests") renderFeatureAccordions();
  if (subView === "dashboard") await loadDashboard();
  if (subView === "settings") loadInlineSettings();
  closeSidebarMobile();
}

// ---------------------------------------------------------------------------
// Project switcher (header)
// ---------------------------------------------------------------------------
function updateProjectSwitcherLabel() {
  const label = el("projectSwitcherLabel");
  const proj = projectsList.find(p => p.id === currentProjectId);
  if (label) label.textContent = proj ? proj.name : "Select Project";
}

function renderProjectSwitcherList() {
  const list = el("projectSwitcherList");
  if (!list) return;
  list.innerHTML = "";
  projectsList.forEach(p => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "w-full text-left px-3 py-2 text-sm flex justify-between items-center";
    btn.style.cssText = p.id === currentProjectId ? "background:var(--accent-subtle);color:var(--accent-text);" : "color:var(--text-primary);";
    btn.innerHTML = `<span class="truncate">${escapeHtml(p.name)}</span><span class="text-xs shrink-0 ml-2" style="color:var(--text-tertiary);">${p.test_case_count} tests</span>`;
    btn.addEventListener("click", () => {
      el("projectSwitcherDropdown")?.classList.add("hidden");
      navigateTo(`/project/${p.id}/overview`);
    });
    list.appendChild(btn);
  });
}

function toggleProjectSwitcherDropdown() {
  const dd = el("projectSwitcherDropdown");
  dd?.classList.toggle("hidden");
}

// ---------------------------------------------------------------------------
// Project grid (home)
// ---------------------------------------------------------------------------
function renderProjectGrid(list) {
  const grid = el("projectGrid");
  if (!grid) return;
  grid.innerHTML = "";
  (list || []).forEach(p => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "text-left rounded-xl p-4 transition-colors shadow-sm";
    card.style.cssText = "background:var(--bg-surface);border:1px solid var(--border-default);color:var(--text-primary);";
    card.innerHTML = `
      <div class="font-semibold truncate">${escapeHtml(p.name)}</div>
      <div class="text-xs mt-1 line-clamp-2" style="color:var(--text-tertiary);">${escapeHtml(p.description || "No description")}</div>
      <div class="text-xs mt-2" style="color:var(--text-tertiary);">${p.feature_count ?? 0} features · ${p.test_case_count ?? 0} tests</div>
      <div class="text-xs mt-1" style="color:var(--text-muted);">Updated ${escapeHtml(formatIST(p.updated_at))}</div>`;
    card.addEventListener("click", () => navigateTo(`/project/${p.id}/overview`));
    grid.appendChild(card);
  });
}

// ---------------------------------------------------------------------------
// Projects CRUD
// ---------------------------------------------------------------------------
async function refreshProjects() {
  projectsList = await fetchJSON("/api/projects");
  renderProjectGrid(projectsList);
  renderProjectSwitcherList();
  updateProjectSwitcherLabel();
  el("authSection")?.classList.add("hidden");
}

function openProjectModal(editId = null) {
  const titleEl = el("projectModalTitle");
  const nameEl = el("projectModalName");
  const descEl = el("projectModalDesc");
  const editIdEl = el("projectModalEditId");
  const descWrap = el("projectModalDescWrap");
  if (editId) {
    titleEl.textContent = "Rename project";
    const fromData = lastProjectData?.project?.name;
    const fromDom = el("projectNameDisplay")?.textContent?.trim();
    nameEl.value = (fromData ?? fromDom ?? "").trim();
    editIdEl.value = editId;
    descWrap?.classList.add("hidden");
  } else {
    titleEl.textContent = "New Project";
    nameEl.value = "";
    descEl.value = "";
    editIdEl.value = "";
    descWrap?.classList.remove("hidden");
  }
  el("projectModal")?.classList.remove("hidden");
  nameEl.focus();
}

async function saveProjectModal() {
  const name = el("projectModalName").value.trim();
  const description = el("projectModalDesc").value.trim();
  const editId = el("projectModalEditId").value;
  if (!name) { fieldError(el("projectModalName"), "Please enter a project name."); return; }
  try {
    if (editId) {
      await fetchJSON(`/api/projects/${editId}`, { method: "PUT", body: JSON.stringify({ name }) });
      showToast("Project renamed.");
    } else {
      const p = await fetchJSON("/api/projects", { method: "POST", body: JSON.stringify({ name, description }) });
      currentProjectId = p.id;
      showToast("Project created.");
      navigateTo(`/project/${p.id}/overview`);
    }
    el("projectModal")?.classList.add("hidden");
    await refreshProjects();
    if (editId) await loadProjectWorkspaceData();
  } catch (e) { showToast(String(e.message || e), true); }
}

async function deleteProject() {
  if (!currentProjectId) return;
  if (!(await customConfirm(
    "All features and test cases in this project will be permanently removed. This action cannot be undone.",
    { title: "Delete this project?", destructive: true, okLabel: "Delete Project" }
  ))) return;
  try {
    await fetchJSON(`/api/projects/${currentProjectId}`, { method: "DELETE" });
    currentProjectId = null; lastProjectData = null; lastRouteProjectId = null;
    await refreshProjects();
    navigateTo("/");
    showToast("Project deleted.");
  } catch (e) { showToast(String(e.message || e), true); }
}

// ---------------------------------------------------------------------------
// Project workspace data
// ---------------------------------------------------------------------------
async function loadProjectWorkspaceData() {
  const id = currentProjectId;
  lastLoadedFeatures = []; lastLoadedCases = []; selectedTcIds.clear(); lastProjectData = null;
  if (!id) return;
  try {
    const detail = await fetchJSON(`/api/projects/${id}`);
    lastProjectData = detail;
    const p = detail.project;
    const nameEl = el("projectNameDisplay");
    const updatedEl = el("projectUpdatedMeta");
    if (nameEl) nameEl.textContent = p.name || "";
    if (updatedEl) updatedEl.textContent = `Updated: ${formatIST(p.updated_at)}`;
    descriptionSection?.setValue(p.description || "");
    baseUrlSection?.setValue(p.base_url || "");
    renderAuthConfig(p.auth_config || {});
    lastLoadedFeatures = detail.features || [];
    await loadAllTestCases();
    renderFeatureAccordions();
  } catch (e) { showToast(String(e.message || e), true); }
}

async function loadAllTestCases() {
  if (!currentProjectId) { lastLoadedCases = []; return; }
  lastLoadedCases = await fetchJSON(`/api/projects/${currentProjectId}/test-cases`);
}

// ---------------------------------------------------------------------------
// Feature details page (Test Cases tab + Generations tab)
// ---------------------------------------------------------------------------
let _featDetailState = { projectId: null, featureId: null, generations: [], activeTab: "cases" };

async function renderFeatureDetailPage(projectId, featureId) {
  _featDetailState.projectId = projectId;
  _featDetailState.featureId = featureId;
  _featDetailState.activeTab = "cases";

  const feature = (lastLoadedFeatures || []).find(f => f.id === featureId);
  const nameInput = el("featDetailName");
  const back = el("featDetailBack");
  if (back) {
    back.onclick = (e) => { e.preventDefault(); navigateTo(`/project/${projectId}/tests`); };
  }
  if (!feature) {
    if (nameInput) nameInput.value = "(Feature not found)";
    el("featTabBody").innerHTML = `<p class="text-sm" style="color:var(--text-tertiary);">Feature not found.</p>`;
    return;
  }
  if (nameInput) nameInput.value = feature.name || "";

  const saveBtn = el("featDetailSave");
  if (saveBtn) {
    saveBtn.onclick = async () => {
      const newName = (nameInput?.value || "").trim();
      if (!newName) { showToast("Feature name cannot be empty.", true); return; }
      try {
        await fetchJSON(`/api/projects/${projectId}/features/${encodeURIComponent(featureId)}`,
          { method: "PUT", body: JSON.stringify({ name: newName }) });
        feature.name = newName;
        showToast("Feature renamed.");
      } catch (e) {
        showToast(String(e.message || e), true);
      }
    };
  }

  // Tab counts will be filled after we load generations
  let generations = [];
  try {
    generations = await fetchJSON(`/api/features/${encodeURIComponent(featureId)}/generations`);
  } catch (e) {
    // Non-fatal — show empty tab
    generations = [];
  }
  _featDetailState.generations = generations;

  const featureCases = (lastLoadedCases || []).filter(tc => tc.feature_id === featureId);
  const casesCountEl = el("featTabCasesCount");
  const gensCountEl = el("featTabGensCount");
  if (casesCountEl) casesCountEl.textContent = String(featureCases.length);
  if (gensCountEl) gensCountEl.textContent = String(generations.length);

  // Wire tab buttons
  const tabCases = el("featTabCases");
  const tabGens = el("featTabGens");
  function setTab(t) {
    _featDetailState.activeTab = t;
    if (tabCases) {
      tabCases.style.borderBottomColor = t === "cases" ? "var(--accent)" : "transparent";
      tabCases.style.color = t === "cases" ? "var(--accent-text)" : "var(--text-tertiary)";
    }
    if (tabGens) {
      tabGens.style.borderBottomColor = t === "gens" ? "var(--accent)" : "transparent";
      tabGens.style.color = t === "gens" ? "var(--accent-text)" : "var(--text-tertiary)";
    }
    renderFeatureDetailTabBody();
  }
  if (tabCases) tabCases.onclick = () => setTab("cases");
  if (tabGens) tabGens.onclick = () => setTab("gens");
  setTab("cases");
}

function renderFeatureDetailTabBody() {
  const body = el("featTabBody");
  if (!body) return;
  const featureId = _featDetailState.featureId;
  const tab = _featDetailState.activeTab;
  const cases = (lastLoadedCases || []).filter(tc => tc.feature_id === featureId);

  if (tab === "cases") {
    if (cases.length === 0) {
      body.innerHTML = `<p class="px-4 py-3 text-sm" style="color:var(--text-tertiary);">No test cases yet for this feature.</p>`;
      return;
    }
    const priStyle = (p) => p === "high"
      ? `background:var(--status-high-bg);color:var(--status-high);`
      : p === "low"
        ? `background:var(--status-low-bg);color:var(--status-low);`
        : `background:var(--status-med-bg);color:var(--status-med);`;
    body.innerHTML = `
      <table class="w-full text-sm">
        <thead class="text-xs uppercase" style="color:var(--text-tertiary);background:var(--bg-surface-alt);">
          <tr>
            <th class="px-4 py-2 text-left w-20">ID</th>
            <th class="px-4 py-2 text-left">Title</th>
            <th class="px-4 py-2 text-left w-24">Type</th>
            <th class="px-4 py-2 text-left w-24">Priority</th>
          </tr>
        </thead>
        <tbody>
          ${cases.map(tc => `
            <tr class="cursor-pointer feat-detail-tc-row" style="border-top:1px solid var(--border-subtle);" data-tc-id="${escapeHtml(tc.id)}">
              <td class="px-4 py-2 font-mono text-xs" style="color:var(--accent-text);">${lastRunDot(tc.last_run_status)}${escapeHtml(tc.id)}</td>
              <td class="px-4 py-2" style="color:var(--text-primary);">${escapeHtml(tc.title)}</td>
              <td class="px-4 py-2"><span class="text-xs px-1.5 py-0.5 rounded" style="background:var(--bg-surface-alt);color:var(--text-secondary);">${escapeHtml(tc.type)}</span></td>
              <td class="px-4 py-2"><span class="text-xs px-1.5 py-0.5 rounded" style="${priStyle(tc.priority)}">${escapeHtml(tc.priority)}</span></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    `;
    body.querySelectorAll(".feat-detail-tc-row").forEach(row => {
      row.addEventListener("click", () => openTcDetail(row.dataset.tcId));
    });
    return;
  }

  // tab === "gens"
  const gens = _featDetailState.generations || [];
  if (gens.length === 0) {
    body.innerHTML = `<p class="px-4 py-3 text-sm" style="color:var(--text-tertiary);">No generations recorded yet. Generate or iterate to populate this tab.</p>`;
    return;
  }
  body.innerHTML = gens.map(entry => renderGenerationCard(entry, cases)).join("");

  body.querySelectorAll("[data-image-input-id]").forEach(img => {
    img.addEventListener("click", () => openImageModal(img.dataset.imageInputId));
  });
  body.querySelectorAll("[data-text-toggle]").forEach(btn => {
    btn.addEventListener("click", () => {
      const parent = btn.parentElement;
      const fullEl = parent.querySelector("[data-text-full]");
      const shortEl = parent.querySelector("[data-text-short]");
      const isExpanded = fullEl.style.display !== "none";
      fullEl.style.display = isExpanded ? "none" : "block";
      shortEl.style.display = isExpanded ? "block" : "none";
      btn.textContent = isExpanded ? "Show more" : "Show less";
    });
  });
  body.querySelectorAll(".gen-output-tc").forEach(link => {
    link.addEventListener("click", (ev) => {
      ev.preventDefault();
      const tcId = link.dataset.tcId;
      if (tcId) openTcDetail(tcId);
    });
  });
}

function renderGenerationCard(entry, casesByFeature) {
  const g = entry.generation || entry;
  const tcIds = entry.test_case_ids || [];
  const matched = (casesByFeature || []).filter(c => tcIds.includes(c.id));
  const dt = formatIST(g.created_at);
  const triggerLabel = g.trigger === "iterate" ? "Iterate" : "Generate";
  const inputsHtml = (g.inputs || []).map(renderInputBlock).join("");
  const outputsHtml = matched.length
    ? `<ul class="list-disc pl-5 text-sm space-y-1">${matched.map(c =>
        `<li><a href="#" class="gen-output-tc hover:underline" data-tc-id="${escapeHtml(c.id)}" style="color:var(--accent-text);">${escapeHtml(c.title)}</a></li>`
      ).join("")}</ul>`
    : `<p class="text-xs" style="color:var(--text-tertiary);">(no test cases linked)</p>`;
  return `
    <div class="rounded-lg p-3 mb-3 shadow-sm" style="background:var(--bg-surface);border:1px solid var(--border-default);">
      <div class="text-sm font-semibold" style="color:var(--text-primary);">${triggerLabel} &middot; ${escapeHtml(dt)} &middot; ${tcIds.length} test case${tcIds.length === 1 ? "" : "s"}</div>
      <div class="text-xs mt-0.5 mb-2" style="color:var(--text-tertiary);">${escapeHtml(g.summary || "")}</div>
      <div class="mb-3">
        <div class="text-xs font-medium mb-1 uppercase tracking-wide" style="color:var(--text-tertiary);">Inputs</div>
        ${inputsHtml || `<p class="text-xs" style="color:var(--text-tertiary);">(no inputs)</p>`}
      </div>
      <div>
        <div class="text-xs font-medium mb-1 uppercase tracking-wide" style="color:var(--text-tertiary);">Outputs</div>
        ${outputsHtml}
      </div>
    </div>
  `;
}

function renderInputBlock(inp) {
  if (inp.source_type === "screenshot" && inp.id) {
    return `
      <div class="mb-2">
        <div class="text-xs mb-1" style="color:var(--text-tertiary);">&#128247; ${escapeHtml(inp.summary || "screenshot")}</div>
        <img src="${API}/api/generation-inputs/${encodeURIComponent(inp.id)}/image"
             data-image-input-id="${encodeURIComponent(inp.id)}"
             alt="${escapeHtml(inp.summary || "screenshot")}"
             style="max-width:160px;max-height:120px;cursor:zoom-in;border:1px solid var(--border-default);border-radius:4px;" />
      </div>
    `;
  }
  if (inp.source_type === "text" && inp.text_content) {
    const short = inp.text_content.slice(0, 300);
    const isTruncated = inp.text_content.length > 300;
    return `
      <div class="mb-2 text-sm">
        <div class="text-xs mb-1" style="color:var(--text-tertiary);">&#128221; ${escapeHtml(inp.summary || "Manual text")}</div>
        <div data-text-short style="display:block;white-space:pre-wrap;color:var(--text-secondary);">${escapeHtml(short)}${isTruncated ? "…" : ""}</div>
        ${isTruncated ? `
          <div data-text-full style="display:none;white-space:pre-wrap;color:var(--text-secondary);">${escapeHtml(inp.text_content)}</div>
          <button type="button" class="text-xs underline mt-1" style="color:var(--accent-text);" data-text-toggle>Show more</button>
        ` : ""}
      </div>
    `;
  }
  if (inp.url) {
    const icon = inp.source_type === "jira" ? "&#129518;" :
                 inp.source_type === "figma" ? "&#128396;" :
                 inp.source_type === "browser_session" ? "&#127760;" : "&#128279;";
    return `
      <div class="mb-2 text-sm">
        ${icon} ${escapeHtml(inp.summary || inp.source_type)} &mdash;
        <a href="${escapeHtml(inp.url)}" target="_blank" rel="noopener" class="hover:underline break-all" style="color:var(--accent-text);">${escapeHtml(inp.url)}</a>
      </div>
    `;
  }
  return `
    <div class="mb-2 text-sm">&bull; ${escapeHtml(inp.summary || inp.source_type || "input")}</div>
  `;
}

function openImageModal(inputId) {
  const modal = el("imageModal");
  const img = el("imageModalImg");
  if (!modal || !img) return;
  img.src = `${API}/api/generation-inputs/${encodeURIComponent(inputId)}/image`;
  modal.classList.remove("hidden");
}
function closeImageModal() {
  el("imageModal")?.classList.add("hidden");
}

// ---------------------------------------------------------------------------
// Editable section (preview <-> edit) — Base URL + Project Description
// ---------------------------------------------------------------------------
let baseUrlSection = null;
let descriptionSection = null;

function makeEditableSection({
  previewEl, editWrapEl, fieldEl,
  previewActionsEl, editActionsEl,
  saveBtn, cancelBtn,
  errorEl, saveOnEnter,
  renderPreview, save,
}) {
  let currentValue = "";
  let saving = false;

  function show(node, on) { node.classList.toggle("hidden", !on); }

  function toPreview() {
    renderPreview(currentValue);
    show(previewEl, true);
    show(previewActionsEl, true);
    show(editWrapEl, false);
    show(editActionsEl, false);
    if (errorEl) errorEl.textContent = "";
  }

  function enterEdit() {
    fieldEl.value = currentValue;
    show(previewEl, false);
    show(previewActionsEl, false);
    show(editWrapEl, true);
    show(editActionsEl, true);
    if (errorEl) errorEl.textContent = "";
    fieldEl.focus();
  }

  async function commit() {
    if (saving) return;
    const next = fieldEl.value;
    if (next === currentValue) { toPreview(); return; }
    saving = true;
    if (errorEl) errorEl.textContent = "";
    try {
      await save(next);
      currentValue = next;
      toPreview();
    } catch (e) {
      if (errorEl) errorEl.textContent = String(e.message || e);
    } finally {
      saving = false;
    }
  }

  saveBtn.addEventListener("click", commit);
  cancelBtn.addEventListener("click", toPreview);
  if (saveOnEnter) {
    fieldEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); commit(); }
      else if (e.key === "Escape") { e.preventDefault(); toPreview(); }
    });
  } else {
    fieldEl.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.preventDefault(); toPreview(); }
    });
  }

  toPreview();

  return {
    enterEdit,
    toPreview,
    setValue(v) { currentValue = v || ""; toPreview(); },
    setFieldValue(v) { fieldEl.value = v || ""; },  // used by draft modal (stays in edit mode)
  };
}

function initOverviewSections() {
  baseUrlSection = makeEditableSection({
    previewEl: el("baseUrlPreview"),
    editWrapEl: el("baseUrlEditWrap"),
    fieldEl: el("projectBaseUrl"),
    previewActionsEl: el("baseUrlPreviewActions"),
    editActionsEl: el("baseUrlEditActions"),
    saveBtn: el("btnSaveBaseUrl"),
    cancelBtn: el("btnCancelBaseUrl"),
    errorEl: el("baseUrlError"),
    saveOnEnter: true,
    renderPreview: (v) => {
      const node = el("baseUrlPreview");
      if (!node) return;
      if (v) { node.textContent = v; node.style.color = "var(--text-primary)"; }
      else { node.textContent = "Not set"; node.style.color = "var(--text-muted)"; }
    },
    save: async (v) => {
      const trimmed = (v || "").trim();
      if (trimmed && !/^https?:\/\//.test(trimmed)) {
        throw new Error("URL must start with http:// or https://");
      }
      await fetchJSON(`/api/projects/${currentProjectId}`, { method: "PUT", body: JSON.stringify({ base_url: trimmed }) });
      if (lastProjectData?.project) lastProjectData.project.base_url = trimmed;
    },
  });

  descriptionSection = makeEditableSection({
    previewEl: el("descPreview"),
    editWrapEl: el("descEditWrap"),
    fieldEl: el("descriptionText"),
    previewActionsEl: el("descPreviewActions"),
    editActionsEl: el("descEditActions"),
    saveBtn: el("btnSaveDescription"),
    cancelBtn: el("btnCancelDescription"),
    errorEl: el("descError"),
    saveOnEnter: false,
    renderPreview: (v) => {
      const node = el("descPreview");
      if (!node) return;
      if (v && v.trim()) { node.textContent = v; node.style.color = "var(--text-secondary)"; }
      else { node.textContent = "No description yet. Click Edit to add one."; node.style.color = "var(--text-muted)"; }
    },
    save: async (v) => {
      await fetchJSON(`/api/projects/${currentProjectId}`, { method: "PUT", body: JSON.stringify({ description: v }) });
      if (lastProjectData?.project) lastProjectData.project.description = v;
    },
  });

  el("btnEditBaseUrl")?.addEventListener("click", () => baseUrlSection.enterEdit());
  el("btnEditDescription")?.addEventListener("click", () => descriptionSection.enterEdit());
  el("btnSaveAuth")?.addEventListener("click", () => saveAuthConfig().catch(e => showToast(String(e.message || e), true)));
  el("btnVerifyAuth")?.addEventListener("click", () => verifyAuthConfig());
}

// ---------------------------------------------------------------------------
// Login setup (authenticated auto-execute)
// ---------------------------------------------------------------------------
function renderAuthConfig(cfg) {
  el("authLoginUrl").value = cfg.login_url || "";
  el("authUsername").value = cfg.username || "";
  el("authLoginPassword").value = "";
  el("authSuccessCheck").value = cfg.success_check || "";
  el("authHomePath").value = cfg.home_path || "";
  const sel = cfg.selectors || {};
  el("authSelUser").value = sel.username || "";
  el("authSelPass").value = sel.password || "";
  el("authSelSubmit").value = sel.submit || "";
  const status = el("authStatus");
  if (cfg.last_error) status.textContent = "Last attempt failed";
  else if (cfg.verified_at) status.textContent = "Session saved · verified";
  else if (cfg.password_set) status.textContent = "Credentials set — not verified";
  else status.textContent = "Not set";
}

function _authBody() {
  const pw = el("authLoginPassword").value;
  const body = {
    login_url: el("authLoginUrl").value.trim(),
    username: el("authUsername").value.trim(),
    success_check: el("authSuccessCheck").value.trim(),
    home_path: el("authHomePath").value.trim(),
    selectors: {
      username: el("authSelUser").value.trim(),
      password: el("authSelPass").value.trim(),
      submit: el("authSelSubmit").value.trim(),
    },
  };
  if (pw) body.password = pw;
  return body;
}

async function saveAuthConfig() {
  el("authError").textContent = "";
  const r = await fetchJSON(`/api/projects/${currentProjectId}/auth`, {
    method: "PUT", body: JSON.stringify(_authBody()),
  });
  renderAuthConfig(r.auth_config || {});
  showToast("Login settings saved.");
}

async function verifyAuthConfig() {
  el("authError").textContent = "";
  el("authShot").classList.add("hidden");
  const btn = el("btnVerifyAuth");
  btn.disabled = true; btn.textContent = "Testing…";
  try {
    await fetchJSON(`/api/projects/${currentProjectId}/auth`, { method: "PUT", body: JSON.stringify(_authBody()) });
    const res = await fetchJSON(`/api/projects/${currentProjectId}/auth/verify`, { method: "POST", body: "{}" });
    if (res.ok) {
      el("authStatus").textContent = "Session saved · verified";
      showToast("Login succeeded — session saved.");
    } else {
      el("authStatus").textContent = "Last attempt failed";
      el("authError").textContent = res.error || "Login failed";
    }
    if (res.screenshot_b64) {
      const img = el("authShot"); img.src = "data:image/jpeg;base64," + res.screenshot_b64;
      img.classList.remove("hidden");
    }
  } catch (e) {
    el("authError").textContent = String(e.message || e);
  } finally {
    btn.disabled = false; btn.textContent = "Test login & save session";
  }
}

// ---------------------------------------------------------------------------
// Draft description from file (modal)
// ---------------------------------------------------------------------------
let _draftAbort = null;

function openDraftModal() {
  setDraftState("pick");
  el("draftFileName").textContent = "";
  el("draftFileInput").value = "";
  el("draftPreviewText").textContent = "";
  el("draftModal")?.classList.remove("hidden");
}

function closeDraftModal() {
  if (_draftAbort) { _draftAbort.abort(); _draftAbort = null; }
  el("draftModal")?.classList.add("hidden");
}

function setDraftState(state) {
  el("draftStatePick")?.classList.toggle("hidden", state !== "pick");
  el("draftStateGenerating")?.classList.toggle("hidden", state !== "generating");
  el("draftStatePreview")?.classList.toggle("hidden", state !== "preview");
  el("btnDraftUse")?.classList.toggle("hidden", state !== "preview");
}

async function draftGenerateFromFile(file) {
  if (!file || !currentProjectId) return;
  el("draftGeneratingMsg").textContent = `Drafting from ${file.name}…`;
  setDraftState("generating");
  _draftAbort = new AbortController();
  try {
    const fd = new FormData();
    fd.append("file", file, file.name);
    const r = await fetch(API + `/api/projects/${currentProjectId}/generate-description`, {
      method: "POST", headers: authHeaders(), body: fd, signal: _draftAbort.signal,
    });
    const text = await r.text();
    const data = parseResponseBody(text);
    if (!r.ok) throw new Error(formatApiError(r.status, data));
    el("draftPreviewText").textContent = data.overview || "";
    const hasExisting = !!(el("descriptionText")?.value || "").trim();
    el("draftReplaceWarning")?.classList.toggle("hidden", !hasExisting);
    setDraftState("preview");
  } catch (e) {
    if (e.name === "AbortError") { setDraftState("pick"); return; }
    showToast(String(e.message || e), true);
    setDraftState("pick");
  } finally {
    _draftAbort = null;
  }
}

function useDraftDescription() {
  const text = el("draftPreviewText").textContent || "";
  descriptionSection?.setFieldValue(text);
  closeDraftModal();
}

// ---------------------------------------------------------------------------
// Feature accordions (Jira-backlog style)
// ---------------------------------------------------------------------------
function renderFeatureAccordions() {
  const container = el("featureAccordions");
  const hint = el("noFeaturesHint");
  if (!container) return;
  container.innerHTML = "";
  if (!lastLoadedFeatures.length) {
    hint?.classList.remove("hidden");
    return;
  }
  hint?.classList.add("hidden");

  lastLoadedFeatures.forEach(feat => {
    const featureCases = lastLoadedCases.filter(tc => tc.feature_id === feat.id);
    const isExpanded = expandedFeatures.has(feat.id);
    const acc = document.createElement("div");
    acc.className = "rounded-lg overflow-hidden shadow-sm";
    acc.style.cssText = "background:var(--bg-surface);border:1px solid var(--border-default);";

    const priStyle = (p) => p === "high" ? `background:var(--status-high-bg);color:var(--status-high);` : p === "low" ? `background:var(--status-low-bg);color:var(--status-low);` : `background:var(--status-med-bg);color:var(--status-med);`;

    acc.innerHTML = `
      <div class="flex items-center justify-between px-4 py-3 cursor-pointer select-none accordion-header" data-fid="${escapeHtml(feat.id)}">
        <div class="flex items-center gap-3 min-w-0">
          <span class="inline-flex items-center justify-center accordion-chevron" style="color:var(--text-muted);">${isExpanded ? svgChevronDown() : svgChevronRight()}</span>
          <span class="feature-name-link font-medium truncate hover:underline" style="color:var(--text-primary);" title="Open feature details" data-fid="${escapeHtml(feat.id)}">${escapeHtml(feat.name)}</span>
          <span class="text-xs px-2 py-0.5 rounded-full shrink-0" style="background:var(--bg-surface-alt);color:var(--text-tertiary);">${featureCases.length}</span>
        </div>
        <div class="flex items-center gap-1.5 shrink-0" onclick="event.stopPropagation()">
          <button type="button" class="text-xs px-2.5 py-1.5 rounded btn-gen-feature" style="background:var(--status-low-bg);color:var(--status-low);" data-fid="${escapeHtml(feat.id)}" data-fname="${escapeHtml(feat.name)}" title="Generate test cases">&#10024; Generate</button>
          <button type="button" class="text-xs px-2.5 py-1.5 rounded btn-iter-feature" style="background:var(--status-med-bg);color:var(--status-med);" data-fid="${escapeHtml(feat.id)}" data-fname="${escapeHtml(feat.name)}" title="Iterate test cases">&#8635; Iterate</button>
          <button type="button" class="text-xs px-2.5 py-1.5 rounded btn-del-feature" style="background:var(--status-high-bg);color:var(--status-high);" data-fid="${escapeHtml(feat.id)}" data-fname="${escapeHtml(feat.name)}" title="Delete feature">&#128465;</button>
        </div>
      </div>
      <div class="accordion-body ${isExpanded ? "" : "hidden"}" style="border-top:1px solid var(--border-default);">
        ${featureCases.length === 0
          ? '<p class="px-4 py-3 text-sm" style="color:var(--text-tertiary);">No test cases yet. Click Generate to create test cases for this feature.</p>'
          : `<table class="w-full text-sm">
              <thead class="text-xs uppercase" style="color:var(--text-tertiary);background:var(--bg-surface-alt);">
                <tr>
                  <th class="px-3 py-2 w-20 text-left"><label class="inline-flex items-center gap-1.5 cursor-pointer normal-case font-normal" style="color:var(--text-tertiary);"><input type="checkbox" class="tc-select-all" data-fid="${escapeHtml(feat.id)}" title="Select all in this feature" /><span>All</span></label></th>
                  <th class="px-4 py-2 text-left w-20">ID</th>
                  <th class="px-4 py-2 text-left">Title</th>
                  <th class="px-4 py-2 text-left w-24">Type</th>
                  <th class="px-4 py-2 text-left w-24">Priority</th>
                  <th class="px-4 py-2 w-10"></th>
                </tr>
              </thead>
              <tbody>
                ${featureCases.map(tc => `
                  <tr class="cursor-pointer tc-row" style="border-top:1px solid var(--border-subtle);" data-tc-id="${escapeHtml(tc.id)}">
                    <td class="px-3 py-2"><input type="checkbox" class="tc-select" data-tc-id="${escapeHtml(tc.id)}" ${selectedTcIds.has(tc.id) ? "checked" : ""} /></td>
                    <td class="px-4 py-2 font-mono text-xs" style="color:var(--accent-text);">${lastRunDot(tc.last_run_status)}${escapeHtml(tc.id)}</td>
                    <td class="px-4 py-2" style="color:var(--text-primary);">${escapeHtml(tc.title)}</td>
                    <td class="px-4 py-2"><span class="text-xs px-1.5 py-0.5 rounded" style="background:var(--bg-surface-alt);color:var(--text-secondary);">${escapeHtml(tc.type)}</span></td>
                    <td class="px-4 py-2"><span class="text-xs px-1.5 py-0.5 rounded" style="${priStyle(tc.priority)}">${escapeHtml(tc.priority)}</span></td>
                    <td class="px-4 py-2" style="color:var(--text-muted);"><span class="inline-flex items-center justify-center opacity-70">${svgChevronRight("w-3.5 h-3.5")}</span></td>
                  </tr>
                `).join("")}
              </tbody>
            </table>`
        }
      </div>`;

    acc.querySelector(".accordion-header").addEventListener("click", () => {
      const body = acc.querySelector(".accordion-body");
      const chevron = acc.querySelector(".accordion-chevron");
      body.classList.toggle("hidden");
      const isOpen = !body.classList.contains("hidden");
      chevron.innerHTML = isOpen ? svgChevronDown() : svgChevronRight();
      if (isOpen) expandedFeatures.add(feat.id); else expandedFeatures.delete(feat.id);
    });

    acc.querySelector(".btn-gen-feature").addEventListener("click", () => openGenerateModal(feat.id, feat.name));
    acc.querySelector(".btn-iter-feature").addEventListener("click", () => openIterateModal(feat.id, feat.name));
    acc.querySelector(".btn-del-feature").addEventListener("click", () => deleteFeature(feat.id, feat.name));

    const nameLink = acc.querySelector(".feature-name-link");
    if (nameLink) {
      nameLink.addEventListener("click", (ev) => {
        ev.stopPropagation();  // don't toggle the accordion
        navigateTo(`/project/${currentProjectId}/feature/${feat.id}`);
      });
    }

    acc.querySelectorAll(".tc-row").forEach(row => {
      row.addEventListener("click", (ev) => {
        // Ignore row clicks that originated on the checkbox cell so selecting
        // a row's checkbox doesn't also open the detail slide-in.
        if (ev.target.closest(".tc-select")) return;
        openTcDetail(row.dataset.tcId);
      });
    });

    acc.querySelectorAll(".tc-select").forEach(cb => {
      cb.addEventListener("click", (ev) => ev.stopPropagation());
      cb.addEventListener("change", (ev) => {
        const id = ev.target.dataset.tcId;
        if (ev.target.checked) selectedTcIds.add(id);
        else selectedTcIds.delete(id);
        updateBulkBar();
        syncSelectAllForFeature(acc, feat.id);
      });
    });

    const selectAll = acc.querySelector(".tc-select-all");
    if (selectAll) {
      selectAll.addEventListener("click", (ev) => ev.stopPropagation());
      selectAll.addEventListener("change", (ev) => {
        const checked = ev.target.checked;
        featureCases.forEach(tc => {
          if (checked) selectedTcIds.add(tc.id);
          else selectedTcIds.delete(tc.id);
        });
        acc.querySelectorAll(".tc-select").forEach(cb => { cb.checked = checked; });
        updateBulkBar();
      });
      syncSelectAllForFeature(acc, feat.id);
    }

    container.appendChild(acc);
  });
  updateBulkBar();
}

function syncSelectAllForFeature(acc, featureId) {
  const selectAll = acc.querySelector(".tc-select-all");
  if (!selectAll) return;
  const cases = lastLoadedCases.filter(tc => tc.feature_id === featureId);
  if (!cases.length) { selectAll.checked = false; selectAll.indeterminate = false; return; }
  const selectedCount = cases.filter(tc => selectedTcIds.has(tc.id)).length;
  selectAll.checked = selectedCount === cases.length;
  selectAll.indeterminate = selectedCount > 0 && selectedCount < cases.length;
}

function updateBulkBar() {
  const bar = el("bulkActionsBar");
  const count = el("bulkSelectedCount");
  if (!bar) return;
  const n = selectedTcIds.size;
  if (count) count.textContent = String(n);
  if (n > 0) bar.classList.remove("hidden"); else bar.classList.add("hidden");
}

function clearBulkSelection() {
  selectedTcIds.clear();
  document.querySelectorAll(".tc-select").forEach(cb => { cb.checked = false; });
  document.querySelectorAll(".tc-select-all").forEach(cb => { cb.checked = false; cb.indeterminate = false; });
  updateBulkBar();
}

async function bulkDeleteSelected() {
  if (!currentProjectId || selectedTcIds.size === 0) return;
  const ids = Array.from(selectedTcIds);
  if (!(await customConfirm(
    `The selected test case${ids.length === 1 ? "" : "s"} will be permanently removed. This action cannot be undone.`,
    { title: `Delete ${ids.length} test case${ids.length === 1 ? "" : "s"}?`, destructive: true, okLabel: "Delete" }
  ))) return;
  try {
    const result = await fetchJSON(`/api/projects/${currentProjectId}/test-cases/bulk-delete`, {
      method: "POST",
      body: JSON.stringify({ ids }),
    });
    showToast(`Deleted ${result.deleted ?? ids.length} test case${(result.deleted ?? ids.length) === 1 ? "" : "s"}.`);
    selectedTcIds.clear();
    await loadProjectWorkspaceData();
    await refreshProjects();
  } catch (e) { showToast(String(e.message || e), true); }
}

async function deleteFeature(featureId, name) {
  if (!currentProjectId) return;
  if (!(await customConfirm(
    `All test cases under "${name}" will be permanently removed. This action cannot be undone.`,
    { title: "Delete this feature?", destructive: true, okLabel: "Delete Feature" }
  ))) return;
  try {
    await fetchJSON(`/api/projects/${currentProjectId}/features/${encodeURIComponent(featureId)}`, { method: "DELETE" });
    expandedFeatures.delete(featureId);
    await loadProjectWorkspaceData();
    await refreshProjects();
    showToast("Feature deleted.");
  } catch (e) { showToast(String(e.message || e), true); }
}

// ---------------------------------------------------------------------------
// Feature create modal
// ---------------------------------------------------------------------------
function openFeatureModal() {
  el("featureModalName").value = "";
  el("featureModal")?.classList.remove("hidden");
  el("featureModalName").focus();
}

async function saveFeatureModal() {
  const name = el("featureModalName").value.trim();
  if (!currentProjectId) { showToast("Please select a project first.", true); return; }
  if (!name) { fieldError(el("featureModalName"), "Please enter a feature name."); return; }
  try {
    await fetchJSON(`/api/projects/${currentProjectId}/features`, { method: "POST", body: JSON.stringify({ name, description: "" }) });
    el("featureModal")?.classList.add("hidden");
    await loadProjectWorkspaceData();
    await refreshProjects();
    showToast("Feature created.");
  } catch (e) { showToast(String(e.message || e), true); }
}

// ---------------------------------------------------------------------------
// Test case detail modal
// ---------------------------------------------------------------------------
async function openAutoExecModal() {
  const tcId = el("tcDetailTcId")?.value;
  if (!tcId || !currentProjectId) return;
  const baseUrl = lastProjectData?.project?.base_url?.trim() || "";
  if (!baseUrl) {
    showToast("Set a Base URL in Project Overview first.", true);
    return;
  }
  const tc = lastLoadedCases.find(c => c.id === tcId);
  if (!tc) return;

  // Populate the read-only test case summary
  el("autoExecTcTitle").textContent = tc.title;
  el("autoExecTcSteps").innerHTML = (tc.steps || []).map(s => `<li>${escapeHtml(s)}</li>`).join("");
  el("autoExecTcExpected").textContent = tc.expected_result || "";
  el("autoExecCode").value = "";
  el("autoExecResult").classList.add("hidden");
  el("autoExecResult").innerHTML = "";
  el("autoExecHeaded").checked = false;
  el("autoExecLoginTest").checked = false;
  el("autoExecModal")?.classList.remove("hidden");

  // Load code: the backend returns previously stored code without an LLM call,
  // and only generates (once) when nothing is stored yet.
  setAutoExecGenerating(true);
  try {
    const resp = await fetchJSON(`/api/projects/${currentProjectId}/test-cases/${encodeURIComponent(tcId)}/generate-playwright`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    el("autoExecCode").value = resp.code || "";
    if (tc) tc.playwright_code = resp.code || "";
  } catch (e) {
    el("autoExecCode").value = "// Failed to generate code: " + String(e.message || e);
  } finally {
    setAutoExecGenerating(false);
  }
}

// Toggle the generating spinner + button state in the auto-execute modal.
function setAutoExecGenerating(on) {
  el("autoExecCodeLoading")?.classList.toggle("hidden", !on);
  const run = el("btnAutoExecRun");
  const save = el("btnAutoExecSave");
  const regen = el("btnAutoExecRegenerate");
  if (run) { run.disabled = on; run.innerHTML = on ? "Generating..." : "&#9654; Run"; }
  if (save) save.disabled = on;
  if (regen) regen.disabled = on;
}

async function regenerateAutoExecCode() {
  const tcId = el("tcDetailTcId")?.value;
  if (!tcId || !currentProjectId) return;
  setAutoExecGenerating(true);
  try {
    const resp = await fetchJSON(`/api/projects/${currentProjectId}/test-cases/${encodeURIComponent(tcId)}/generate-playwright`, {
      method: "POST",
      body: JSON.stringify(el("autoExecLoginTest")?.checked ? { regenerate: true, login_mode: true } : { regenerate: true }),
    });
    el("autoExecCode").value = resp.code || "";
    const tc = lastLoadedCases.find(c => c.id === tcId);
    if (tc) tc.playwright_code = resp.code || "";
    showToast("Code regenerated.");
  } catch (e) {
    showToast(String(e.message || e), true);
  } finally {
    setAutoExecGenerating(false);
  }
}

async function saveAutoExecCode() {
  const tcId = el("tcDetailTcId")?.value;
  if (!tcId || !currentProjectId) return;
  const code = el("autoExecCode")?.value || "";
  const btn = el("btnAutoExecSave");
  if (btn) { btn.disabled = true; btn.textContent = "Saving..."; }
  try {
    await fetchJSON(`/api/projects/${currentProjectId}/test-cases/${encodeURIComponent(tcId)}/save-playwright`, {
      method: "POST", body: JSON.stringify({ code }),
    });
    const tc = lastLoadedCases.find(c => c.id === tcId);
    if (tc) tc.playwright_code = code;
    showToast("Code saved.");
  } catch (e) {
    showToast(String(e.message || e), true);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "Save code"; }
  }
}

async function runAutoExec() {
  const tcId = el("tcDetailTcId")?.value;
  if (!tcId || !currentProjectId) return;
  const code = el("autoExecCode")?.value || "";
  const headless = !el("autoExecHeaded")?.checked;
  const resultEl = el("autoExecResult");
  resultEl.classList.remove("hidden");
  resultEl.innerHTML = `<div class="rounded-lg p-4 text-sm" style="background:var(--bg-surface-alt);color:var(--text-secondary);">Running test (max 60s)...</div>`;
  el("btnAutoExecRun").disabled = true;
  el("btnAutoExecRun").textContent = "Running...";
  try {
    const resp = await fetchJSON(`/api/projects/${currentProjectId}/test-cases/${encodeURIComponent(tcId)}/run-playwright`, {
      method: "POST",
      body: JSON.stringify({ code, headless, logged_out: !!el("autoExecLoginTest")?.checked }),
    });
    renderAutoExecResult(resp);
    // Refresh test case in the local cache so the row's status dot updates
    if (resp.status) {
      const tc = lastLoadedCases.find(c => c.id === tcId);
      if (tc) {
        tc.last_run_status = resp.status;
        tc.last_run_at = new Date().toISOString();
        tc.playwright_code = code;  // backend persists the run code; keep cache in sync
        renderFeatureAccordions();
      }
    }
  } catch (e) {
    resultEl.innerHTML = `<div class="rounded-lg p-4 text-sm" style="background:var(--status-high-bg);color:var(--status-high);">${escapeHtml(String(e.message || e))}</div>`;
  } finally {
    el("btnAutoExecRun").disabled = false;
    el("btnAutoExecRun").innerHTML = "&#9654; Run";
  }
}

function renderAutoExecResult(resp) {
  const resultEl = el("autoExecResult");
  const status = resp.status || "error";
  const isPass = status === "passed";
  const chipBg = isPass ? "var(--status-low-bg)" : status === "failed" ? "var(--status-high-bg)" : "var(--status-med-bg)";
  const chipColor = isPass ? "var(--status-low)" : status === "failed" ? "var(--status-high)" : "var(--status-med)";
  const chipText = status.toUpperCase();
  const duration = resp.duration_ms ? `${(resp.duration_ms / 1000).toFixed(1)}s` : "";
  const screenshot = resp.screenshot_b64
    ? `<img src="data:image/jpeg;base64,${resp.screenshot_b64}" class="max-w-full rounded mt-3" style="max-height:480px;border:1px solid var(--border-default);" />`
    : "";
  const errorBlock = resp.error_message
    ? `<pre class="text-xs mt-3 p-3 rounded overflow-x-auto whitespace-pre-wrap" style="background:var(--bg-surface-alt);color:var(--status-high);border:1px solid var(--border-default);">${escapeHtml(resp.error_message)}</pre>`
    : "";
  const consoleBlock = (resp.console_log || "").trim()
    ? `<details class="mt-2"><summary class="text-xs cursor-pointer" style="color:var(--text-tertiary);">Console output (${(resp.console_log || "").split("\n").length} lines)</summary><pre class="text-xs mt-1 p-3 rounded max-h-40 overflow-y-auto whitespace-pre-wrap" style="background:var(--bg-surface-alt);color:var(--text-secondary);">${escapeHtml(resp.console_log)}</pre></details>`
    : "";

  resultEl.innerHTML = `
    <div class="rounded-lg p-4" style="background:var(--bg-surface-alt);border:1px solid var(--border-default);">
      <div class="flex items-center gap-3">
        <span class="px-3 py-1 rounded text-xs font-semibold" style="background:${chipBg};color:${chipColor};">${chipText}</span>
        <span class="text-xs" style="color:var(--text-tertiary);">${duration}</span>
      </div>
      ${screenshot}
      ${errorBlock}
      ${consoleBlock}
    </div>`;

  // When the run failed (assertion mismatch), offer to adapt the expected_result.
  // Hidden for `passed` (no need) and `error` (runner-level crash, not a divergence).
  if (status === "failed") {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.id = "btnMarkAsExpected";
    btn.className = "mt-3 px-3 py-1.5 rounded text-xs font-medium";
    btn.style.cssText = "background:var(--accent);color:var(--text-on-accent);";
    btn.textContent = "✓ Mark as expected behavior";
    btn.addEventListener("click", () => openAdaptExpectedModal(resp));
    resultEl.appendChild(btn);
  }
}

// ---------------------------------------------------------------------------
// "Mark as expected behavior" — adapt a test case's expected_result from
// the observed page behavior on a failed Auto-execute run.
// ---------------------------------------------------------------------------

// State for the currently-open adapt modal so save/regen know what they're
// adapting. Set by openAdaptExpectedModal; cleared by close handlers.
let _adaptContext = null;  // { tcId, runResult, errorMessage, pageText } | null

function _extractAdaptInputs(runResult) {
  // The wrapper's _page_context() emits "URL: ...\nTitle: ...\nPage text (first 240 chars): <text>"
  // appended to error_message after "\n\n". Split out the page text snippet.
  const msg = String(runResult?.error_message || "");
  const sep = "\n\nURL:";
  const idx = msg.indexOf(sep);
  let baseMsg = msg;
  let pageText = "";
  if (idx >= 0) {
    baseMsg = msg.slice(0, idx).trim();
    const tail = msg.slice(idx + 2);  // keep "URL: ...\nTitle: ...\nPage text:"
    const mp = tail.match(/Page text \(first \d+ chars\):\s*([\s\S]*)$/);
    if (mp) pageText = mp[1].trim();
    else pageText = tail.trim();
  }
  return { baseMsg, pageText };
}

async function openAdaptExpectedModal(runResult) {
  const tcId = el("tcDetailTcId")?.value;
  if (!tcId) { showToast("Adapt: no test case id in detail panel — reopen the test case.", true); return; }
  if (!currentProjectId) { showToast("Adapt: no project selected.", true); return; }
  const tc = lastLoadedCases.find(c => c.id === tcId);
  if (!tc) { showToast(`Adapt: test case ${tcId} not found in local cache. Hard-refresh and try again.`, true); return; }
  const modalEl = el("adaptExpectedModal");
  if (!modalEl) {
    showToast("Adapt modal element missing — your browser may have a stale index.html. Hard-refresh (Ctrl+F5) and try again.", true);
    return;
  }
  const { baseMsg, pageText } = _extractAdaptInputs(runResult);

  _adaptContext = { tcId, runResult, errorMessage: baseMsg, pageText };

  el("adaptOriginalExpected").textContent = tc.expected_result || "(empty)";
  el("adaptObservedText").textContent = pageText || "(no page text captured)";
  el("adaptSuggestedText").value = "Loading suggestion...";
  el("adaptSuggestedCode").value = "Loading suggestion...";
  el("btnAdaptExpectedSave").disabled = true;
  modalEl.classList.remove("hidden");

  await _fetchAdaptSuggestion();
}

async function _fetchAdaptSuggestion() {
  if (!_adaptContext) return;
  const { tcId, errorMessage, runResult } = _adaptContext;
  const tc = lastLoadedCases.find(c => c.id === tcId);
  const currentCode = tc?.playwright_code || (el("autoExecCode")?.value || "");
  el("adaptSuggestedText").value = "Loading suggestion...";
  el("adaptSuggestedCode").value = "Loading suggestion...";
  el("btnAdaptExpectedSave").disabled = true;
  try {
    const resp = await fetchJSON(
      `/api/projects/${currentProjectId}/test-cases/${encodeURIComponent(tcId)}/heal`,
      { method: "POST", body: JSON.stringify({
          current_code: currentCode,
          page_snapshot: runResult?.page_snapshot || "",
          error_message: errorMessage,
        }) },
    );
    el("adaptSuggestedText").value = resp.suggested_expected || "";
    el("adaptSuggestedCode").value = resp.suggested_code || "";
    el("btnAdaptExpectedSave").disabled = false;
  } catch (e) {
    el("adaptSuggestedText").value = "";
    el("adaptSuggestedCode").value = "";
    showToast(String(e.message || e), true);
    el("btnAdaptExpectedSave").disabled = false;
  }
}

function closeAdaptExpectedModal() {
  el("adaptExpectedModal")?.classList.add("hidden");
  _adaptContext = null;
}

async function saveAndRerunAdaptedExpected() {
  if (!_adaptContext) return;
  const { tcId } = _adaptContext;
  if (!tcId || !currentProjectId) return;
  const newExpected = (el("adaptSuggestedText")?.value || "").trim();
  if (!newExpected) {
    fieldError(el("adaptSuggestedText"), "Adapted expected result cannot be empty.");
    return;
  }
  const newCode = (el("adaptSuggestedCode")?.value || "").trim();

  el("btnAdaptExpectedSave").disabled = true;
  el("btnAdaptExpectedSave").textContent = "Saving...";

  // Step 1: PATCH the test case
  try {
    await fetchJSON(
      `/api/projects/${currentProjectId}/test-cases/${encodeURIComponent(tcId)}`,
      { method: "PATCH", body: JSON.stringify({ expected_result: newExpected }) },
    );
    // Update local cache so the row reflects the new expected_result
    const tc = lastLoadedCases.find(c => c.id === tcId);
    if (tc) tc.expected_result = newExpected;
  } catch (e) {
    el("btnAdaptExpectedSave").disabled = false;
    el("btnAdaptExpectedSave").textContent = "Save (replaces expected result)";
    showToast(String(e.message || e), true);
    return;
  }

  if (newCode) {
    try {
      await fetchJSON(
        `/api/projects/${currentProjectId}/test-cases/${encodeURIComponent(tcId)}/save-playwright`,
        { method: "POST", body: JSON.stringify({ code: newCode }) });
      const tc2 = lastLoadedCases.find(c => c.id === tcId);
      if (tc2) tc2.playwright_code = newCode;
    } catch (e) { showToast(String(e.message || e), true); }
  }

  // Step 2: close the adapt modal and switch the result panel to "running"
  closeAdaptExpectedModal();
  const resultEl = el("autoExecResult");
  if (resultEl) {
    resultEl.classList.remove("hidden");
    resultEl.innerHTML = `<div class="rounded-lg p-4 text-sm" style="background:var(--bg-surface-alt);color:var(--text-secondary);">Adapted. Re-running test (max 60s)...</div>`;
  }

  // Step 3: run the healed code
  try {
    const resp = await fetchJSON(
      `/api/projects/${currentProjectId}/test-cases/${encodeURIComponent(tcId)}/run-playwright`,
      { method: "POST", body: JSON.stringify({ code: newCode, headless: true }) },
    );
    renderAutoExecResult(resp);
    // If still FAILED after adapt, show inline "still failed" note
    if (resp.status === "failed" && resultEl) {
      const note = document.createElement("div");
      note.className = "rounded-lg p-3 text-xs mt-3";
      note.style.cssText = "background:var(--status-med-bg);color:var(--status-med);border:1px solid var(--status-med);";
      note.textContent = "Adapted, but the test still fails. The page may have changed since the test was written, or the code may need a manual tweak.";
      resultEl.appendChild(note);
    }
    // Update local cache for the status dot
    if (resp.status) {
      const tc = lastLoadedCases.find(c => c.id === tcId);
      if (tc) {
        tc.last_run_status = resp.status;
        tc.last_run_at = new Date().toISOString();
        renderFeatureAccordions();
      }
    }
  } catch (e) {
    if (resultEl) {
      resultEl.innerHTML = `<div class="rounded-lg p-4 text-sm" style="background:var(--status-high-bg);color:var(--status-high);">${escapeHtml(String(e.message || e))}</div>`;
    }
  }
}

function openTcDetail(tcId) {
  const tc = lastLoadedCases.find(c => c.id === tcId);
  if (!tc) return;
  el("tcDetailTcId").value = tc.id;
  el("tcDetailId").textContent = tc.id;
  el("tcDetailTitleDisplay").textContent = tc.title;
  el("tcDetailTitle").value = tc.title;
  el("tcDetailFeature").value = tc.feature || "";
  el("tcDetailPre").value = tc.preconditions || "";
  el("tcDetailSteps").value = (tc.steps || []).join("\n");
  el("tcDetailExpected").value = tc.expected_result || "";
  el("tcDetailSource").value = tc.source_ref || "";
  el("tcDetailCreatedAt").textContent = tc.created_at ? `Created: ${formatIST(tc.created_at)}` : "";

  const typeSelect = el("tcDetailType");
  typeSelect.innerHTML = TEST_TYPES.map(t => `<option value="${t}" ${t === tc.type ? "selected" : ""}>${t}</option>`).join("");
  el("tcDetailPriority").value = tc.priority || "medium";

  el("tcDetailModal")?.classList.remove("hidden");
}

async function saveTcDetail() {
  const tcId = el("tcDetailTcId").value;
  if (!tcId || !currentProjectId) return;
  const body = {
    title: el("tcDetailTitle").value.trim(),
    type: el("tcDetailType").value,
    priority: el("tcDetailPriority").value,
    preconditions: el("tcDetailPre").value,
    steps: el("tcDetailSteps").value.split("\n").map(s => s.trim()).filter(Boolean),
    expected_result: el("tcDetailExpected").value,
    source_ref: el("tcDetailSource").value,
  };
  try {
    showLoading("Saving...");
    await fetchJSON(`/api/projects/${currentProjectId}/test-cases/${encodeURIComponent(tcId)}`, {
      method: "PATCH", body: JSON.stringify(body),
    });
    el("tcDetailModal")?.classList.add("hidden");
    await loadAllTestCases();
    renderFeatureAccordions();
    showToast("Test case saved.");
  } catch (e) { showToast(String(e.message || e), true); }
  finally { hideLoading(); }
}

async function deleteTcFromDetail() {
  const tcId = el("tcDetailTcId").value;
  if (!tcId || !currentProjectId) return;
  if (!(await customConfirm(
    `Test case ${tcId} will be permanently removed. This action cannot be undone.`,
    { title: "Delete this test case?", destructive: true, okLabel: "Delete" }
  ))) return;
  try {
    showLoading("Deleting...");
    await fetchJSON(`/api/projects/${currentProjectId}/test-cases/${encodeURIComponent(tcId)}`, { method: "DELETE" });
    el("tcDetailModal")?.classList.add("hidden");
    await loadAllTestCases();
    renderFeatureAccordions();
    await refreshProjects();
    showToast("Test case deleted.");
  } catch (e) { showToast(String(e.message || e), true); }
  finally { hideLoading(); }
}

// ---------------------------------------------------------------------------
// Test-type multi-select widget (used by Generate + Iterate modals)
// ---------------------------------------------------------------------------
let genPrefTypesWidget = null;
let iterPrefTypesWidget = null;

function mountTestTypeMultiSelect({ containerId }) {
  const container = document.getElementById(containerId);
  if (!container) return null;
  container.innerHTML = "";
  container.classList.add("relative", "mt-1");

  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "w-full rounded px-3 py-2 text-sm text-left flex items-center justify-between";
  trigger.style.background = "var(--bg-input)";
  trigger.style.border = "1px solid var(--border-input)";
  trigger.style.color = "var(--text-primary)";
  trigger.innerHTML = `<span class="ttms-label">Any</span><span aria-hidden="true" style="color:var(--text-muted);">▾</span>`;

  const panel = document.createElement("div");
  panel.className = "hidden absolute left-0 right-0 z-20 mt-1 rounded shadow-lg";
  panel.style.background = "var(--bg-surface)";
  panel.style.border = "1px solid var(--border-default)";
  panel.style.maxHeight = "14rem";
  panel.style.overflowY = "auto";

  TEST_TYPES.forEach(t => {
    const row = document.createElement("label");
    row.className = "flex items-center gap-2 px-3 py-1.5 text-sm cursor-pointer";
    row.style.color = "var(--text-secondary)";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = t;
    cb.className = "shrink-0";
    const txt = document.createElement("span");
    txt.textContent = t;
    row.appendChild(cb);
    row.appendChild(txt);
    panel.appendChild(row);
  });

  container.appendChild(trigger);
  container.appendChild(panel);

  const labelEl = trigger.querySelector(".ttms-label");
  function updateLabel() {
    const sel = Array.from(panel.querySelectorAll("input[type=checkbox]:checked")).map(cb => cb.value);
    if (sel.length === 0) {
      labelEl.textContent = "Any";
      labelEl.style.color = "var(--text-muted)";
    } else if (sel.length <= 2) {
      labelEl.textContent = sel.join(", ");
      labelEl.style.color = "var(--text-primary)";
    } else {
      labelEl.textContent = `${sel.length} types selected`;
      labelEl.style.color = "var(--text-primary)";
    }
  }
  panel.addEventListener("change", updateLabel);

  const isOpen = () => !panel.classList.contains("hidden");
  const open = () => panel.classList.remove("hidden");
  const close = () => panel.classList.add("hidden");

  trigger.addEventListener("click", (e) => {
    e.stopPropagation();
    if (isOpen()) close(); else open();
  });
  document.addEventListener("click", (e) => {
    if (!isOpen()) return;
    if (container.contains(e.target)) return;
    close();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && isOpen()) close();
  });

  updateLabel();

  return {
    getValues() {
      return Array.from(panel.querySelectorAll("input[type=checkbox]:checked")).map(cb => cb.value);
    },
    setValues(arr) {
      const set = new Set((arr || []).map(s => String(s).toLowerCase()));
      panel.querySelectorAll("input[type=checkbox]").forEach(cb => { cb.checked = set.has(cb.value); });
      updateLabel();
    },
    reset() {
      panel.querySelectorAll("input[type=checkbox]").forEach(cb => { cb.checked = false; });
      close();
      updateLabel();
    },
  };
}

// ---------------------------------------------------------------------------
// Image drop-zone (screenshot parser file picker)
// ---------------------------------------------------------------------------
function createImageDropZone({ id }) {
  const wrap = document.createElement("div");
  wrap.className = "w-full";

  const fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.accept = "image/png,image/jpeg,image/webp,image/gif";
  fileInput.id = id;
  fileInput.className = "hidden";

  const zone = document.createElement("div");
  zone.setAttribute("role", "button");
  zone.setAttribute("tabindex", "0");
  zone.className = "w-full rounded-lg p-6 text-center cursor-pointer transition-colors";
  zone.style.background = "var(--bg-input)";
  zone.style.border = "2px dashed var(--border-input)";
  zone.style.position = "relative";

  function renderEmpty() {
    zone.innerHTML = `
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" style="color:var(--accent);display:block;margin:0 auto 8px;">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
      </svg>
      <div class="text-sm font-medium" style="color:var(--text-primary);">Add a screenshot here</div>
      <div class="text-xs mt-1" style="color:var(--text-tertiary);">Click to browse or drag &amp; drop</div>
      <div class="text-xs mt-2" style="color:var(--text-muted);">PNG, JPG, WEBP or GIF · single image</div>
    `;
  }

  function renderSelected(file) {
    const sizeKB = file.size ? (file.size / 1024).toFixed(1) + " KB" : "";
    zone.innerHTML = `
      <button type="button" class="dz-remove" aria-label="Remove selected screenshot" style="position:absolute;top:6px;right:8px;width:24px;height:24px;display:inline-flex;align-items:center;justify-content:center;border-radius:9999px;background:var(--bg-surface);border:1px solid var(--border-default);color:var(--text-muted);font-size:18px;line-height:1;cursor:pointer;">&times;</button>
      <div class="flex items-center justify-center gap-3">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" style="color:var(--accent);flex-shrink:0;display:block;">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/>
        </svg>
        <div class="text-left min-w-0">
          <div class="text-sm font-medium truncate" style="color:var(--text-primary);max-width:260px;">${escapeHtml(file.name || "image")}</div>
          <div class="text-xs" style="color:var(--text-tertiary);">${sizeKB} · click to change</div>
        </div>
      </div>
    `;
  }

  function setFile(file) {
    if (!file) {
      fileInput.value = "";
      renderEmpty();
      return;
    }
    if (file.type && !file.type.startsWith("image/")) {
      showToast("Please choose an image file (PNG, JPG, WEBP, or GIF).", true);
      return;
    }
    try {
      const dt = new DataTransfer();
      dt.items.add(file);
      fileInput.files = dt.files;
    } catch (_) {
      // DataTransfer not supported — fall back to letting the user pick via dialog.
    }
    renderSelected(file);
  }

  function clearFile() {
    fileInput.value = "";
    renderEmpty();
  }
  zone.addEventListener("click", (e) => {
    if (e.target.closest && e.target.closest(".dz-remove")) {
      e.preventDefault();
      e.stopPropagation();
      clearFile();
      return;
    }
    fileInput.click();
  });
  zone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fileInput.click();
    }
  });
  fileInput.addEventListener("change", () => {
    const f = fileInput.files?.[0];
    if (f) renderSelected(f); else renderEmpty();
  });
  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.style.borderColor = "var(--accent)";
  });
  zone.addEventListener("dragleave", () => {
    zone.style.borderColor = "var(--border-input)";
  });
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.style.borderColor = "var(--border-input)";
    const f = e.dataTransfer?.files?.[0];
    if (f) setFile(f);
  });

  renderEmpty();
  wrap.appendChild(fileInput);
  wrap.appendChild(zone);
  return wrap;
}

// ---------------------------------------------------------------------------
// Parsers
// ---------------------------------------------------------------------------
function buildField(f, id) {
  // Checkbox: inline label + box, no full-width styling.
  if (f.type === "checkbox") {
    const wrap = document.createElement("label");
    wrap.className = "flex items-center gap-2 text-sm cursor-pointer select-none";
    wrap.style.color = "var(--text-secondary)";
    wrap.setAttribute("for", id);
    const input = document.createElement("input");
    input.type = "checkbox";
    input.id = id; input.dataset.fieldName = f.name;
    wrap.appendChild(input);
    const txt = document.createElement("span");
    txt.textContent = f.label || f.name;
    wrap.appendChild(txt);
    return wrap;
  }
  const label = document.createElement("label");
  label.className = "block text-xs mb-1";
  label.style.color = "var(--text-tertiary)";
  label.textContent = f.label || f.name;
  label.setAttribute("for", id);
  let input;
  if (f.type === "textarea") { input = document.createElement("textarea"); input.rows = 5; }
  else { input = document.createElement("input"); input.type = f.type === "url" ? "url" : f.type === "number" ? "number" : "text"; }
  input.className = "w-full rounded px-3 py-2 text-sm";
  input.style.cssText = "background:var(--bg-input);border:1px solid var(--border-input);color:var(--text-primary);";
  input.id = id; input.dataset.fieldName = f.name;
  if (f.placeholder) input.placeholder = f.placeholder;
  input.required = !!f.required;
  const wrap = document.createElement("div");
  wrap.appendChild(label); wrap.appendChild(input);
  return wrap;
}

function fieldInput(f, prefix) { return buildField(f, `${prefix}_${f.name}`); }
function fieldInputMulti(f, blockIdx) { return buildField(f, `pf_m_${blockIdx}_${f.name}`); }

function readFieldValue(inp) {
  return inp.type === "checkbox" ? inp.checked : inp.value;
}

function renderGenParsers() {
  const tabs = el("genParserTabs");
  const forms = el("genParserForms");
  if (!tabs || !forms) return;
  tabs.innerHTML = ""; forms.innerHTML = "";
  if (!parsers.length) {
    forms.innerHTML = '<p class="text-sm text-gray-500">No input sources loaded. Check API connection.</p>';
    return;
  }
  parsers.forEach(p => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = p.display_name;
    btn.className = "px-3 py-1.5 rounded text-sm";
    btn.style.cssText = p.name === activeParser ? "background:var(--accent);color:var(--text-on-accent);border:1px solid var(--accent);" : "background:var(--bg-surface);border:1px solid var(--border-input);color:var(--text-secondary);";
    btn.addEventListener("click", () => { activeParser = p.name; renderGenParsers(); });
    tabs.appendChild(btn);
  });
  if (!activeParser && parsers.length) activeParser = parsers[0].name;
  const p = parsers.find(x => x.name === activeParser);
  if (!p) return;

  if (p.name === "browser_session") {
    renderBrowserSessionForm(forms, p);
    updateGenButton();
    return;
  }

  const box = document.createElement("div");
  box.className = "space-y-3";
  const desc = document.createElement("p");
  desc.className = "text-sm";
  desc.style.color = "var(--text-tertiary)";
  desc.textContent = p.description || "";
  box.appendChild(desc);
  (p.input_fields || []).forEach(f => {
    const w = fieldInput(f, "pf");
    if (f.name === "feature_name") w.classList.add("hidden");
    box.appendChild(w);
  });
  if (p.accepts_file) {
    const wrap = document.createElement("div");
    const lab = document.createElement("label");
    lab.className = "block text-xs mb-1";
    lab.style.color = "var(--text-tertiary)";
    lab.textContent = "Screenshot";
    const zone = createImageDropZone({ id: "parser_file" });
    wrap.appendChild(lab); wrap.appendChild(zone); box.appendChild(wrap);

    const hint = document.createElement("p");
    hint.className = "text-xs";
    hint.style.color = "var(--text-tertiary)";
    hint.innerHTML = "Need to analyze multiple screenshots? Enable <strong>Combine multiple sources</strong> above and add a Screenshot block per image.";
    box.appendChild(hint);
  }
  forms.appendChild(box);
  updateGenButton();
}

// ---------------------------------------------------------------------------
// Browser Session custom form
// ---------------------------------------------------------------------------
function renderBrowserSessionForm(container, parserMeta) {
  container.innerHTML = "";
  const box = document.createElement("div");
  box.className = "space-y-4";

  const desc = document.createElement("p");
  desc.className = "text-sm";
  desc.style.color = "var(--text-tertiary)";
  desc.textContent = parserMeta.description || "";
  box.appendChild(desc);

  // Completed session — show the recorded view regardless of mode.
  if (browserSessionId && browserSessionStatus === "completed") {
    renderBrowserSessionRecorded(box);
    container.appendChild(box);
    return;
  }

  // AI exploration mode has its own running view.
  if (bsMode === "ai_explore" && browserSessionId && bsExploreSummary && bsExploreSummary.status === "running") {
    renderAiExploreRunning(box);
    container.appendChild(box);
    return;
  }

  // Manual recording in progress.
  if (bsMode === "manual" && browserSessionId && browserSessionStatus === "recording") {
    renderBrowserSessionProgress(box);
    container.appendChild(box);
    return;
  }

  // Idle state — show the mode toggle and the appropriate form.
  const modeRow = document.createElement("div");
  modeRow.className = "flex gap-2 mb-2";
  modeRow.innerHTML = `
    <button type="button" id="bs_mode_manual" class="px-3 py-1.5 rounded text-xs font-medium" style="${bsMode === 'manual' ? 'background:var(--accent);color:#fff;' : 'background:var(--btn-neutral);color:var(--btn-neutral-text);border:1px solid var(--border-input);'}">Manual recording</button>
    <button type="button" id="bs_mode_ai" class="px-3 py-1.5 rounded text-xs font-medium" style="${bsMode === 'ai_explore' ? 'background:var(--accent);color:#fff;' : 'background:var(--btn-neutral);color:var(--btn-neutral-text);border:1px solid var(--border-input);'}">AI exploration</button>`;
  box.appendChild(modeRow);
  setTimeout(() => {
    el("bs_mode_manual")?.addEventListener("click", () => { bsMode = "manual"; renderGenParsers(); });
    el("bs_mode_ai")?.addEventListener("click", () => { bsMode = "ai_explore"; renderGenParsers(); });
  }, 0);

  if (bsMode === "ai_explore") {
    renderAiExploreIdleForm(box);
    container.appendChild(box);
    return;
  }

  const urlWrap = document.createElement("div");
  urlWrap.innerHTML = `
    <label class="block text-xs mb-1" style="color:var(--text-tertiary);">Target URL</label>
    <input id="bs_url" type="url" class="w-full rounded px-3 py-2 text-sm" style="background:var(--bg-input);border:1px solid var(--border-input);color:var(--text-primary);" placeholder="https://example.com/app" />`;
  box.appendChild(urlWrap);

  const stepsWrap = document.createElement("div");
  stepsWrap.innerHTML = `
    <label class="block text-xs mb-1" style="color:var(--text-tertiary);">Steps (one per line)</label>
    <textarea id="bs_steps" rows="6" class="w-full rounded px-3 py-2 text-sm" style="background:var(--bg-input);border:1px solid var(--border-input);color:var(--text-primary);" placeholder="Click the Login button&#10;Type admin in the username field&#10;Type password123 in the password field&#10;Click Submit&#10;Verify the dashboard loads"></textarea>`;
  box.appendChild(stepsWrap);

  const browserWrap = document.createElement("div");
  browserWrap.innerHTML = `
    <label class="block text-xs mb-1" style="color:var(--text-tertiary);">Browser type</label>
    <select id="bs_browser_type" class="w-full rounded px-3 py-2 text-sm" style="background:var(--bg-input);border:1px solid var(--border-input);color:var(--text-primary);">
      <option value="playwright">Playwright (separate browser)</option>
      <option value="ide_browser">Cursor IDE Browser</option>
    </select>`;
  box.appendChild(browserWrap);

  const btnRow = document.createElement("div");
  btnRow.className = "flex gap-3";
  const recordBtn = document.createElement("button");
  recordBtn.type = "button";
  recordBtn.id = "btnBsRecord";
  recordBtn.className = "px-4 py-2 rounded text-sm font-medium text-white";
  recordBtn.style.cssText = "background:var(--accent);";
  recordBtn.innerHTML = `<span class="flex items-center gap-2"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><circle cx="12" cy="12" r="5" fill="currentColor"/></svg> Start Recording</span>`;
  recordBtn.addEventListener("click", startBrowserSession);
  btnRow.appendChild(recordBtn);
  box.appendChild(btnRow);

  container.appendChild(box);
}

function renderBrowserSessionProgress(box) {
  const header = document.createElement("div");
  header.className = "flex items-center gap-2 mb-2";
  header.innerHTML = `
    <span class="relative flex h-3 w-3"><span class="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style="background:var(--accent);"></span><span class="relative inline-flex rounded-full h-3 w-3" style="background:var(--accent);"></span></span>
    <span class="text-sm font-medium" style="color:var(--accent-text);">Recording in progress...</span>`;
  box.appendChild(header);

  const info = document.createElement("p");
  info.className = "text-xs mb-3";
  info.style.color = "var(--text-tertiary)";
  info.textContent = `Session: ${browserSessionId}`;
  box.appendChild(info);

  renderBrowserSessionStepList(box);

  const addRow = document.createElement("div");
  addRow.className = "flex gap-2 mt-3";
  addRow.innerHTML = `
    <input id="bs_new_step" type="text" class="flex-1 rounded px-3 py-2 text-sm" style="background:var(--bg-input);border:1px solid var(--border-input);color:var(--text-primary);" placeholder="Describe the next step..." />
    <button type="button" id="btnBsAddStep" class="px-3 py-2 rounded text-sm font-medium text-white" style="background:var(--accent);">Add Step</button>`;
  box.appendChild(addRow);

  const actionRow = document.createElement("div");
  actionRow.className = "flex gap-3 mt-3";
  const completeBtn = document.createElement("button");
  completeBtn.type = "button";
  completeBtn.className = "px-4 py-2 rounded text-sm font-medium";
  completeBtn.style.cssText = "background:var(--accent);color:var(--text-on-accent);";
  completeBtn.textContent = "Complete Recording";
  completeBtn.addEventListener("click", completeBrowserSession);
  actionRow.appendChild(completeBtn);

  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "px-4 py-2 rounded text-sm font-medium";
  cancelBtn.style.cssText = "background:var(--btn-neutral);color:var(--btn-neutral-text);border:1px solid var(--border-input);";
  cancelBtn.textContent = "Cancel";
  cancelBtn.addEventListener("click", cancelBrowserSession);
  actionRow.appendChild(cancelBtn);
  box.appendChild(actionRow);

  setTimeout(() => {
    el("btnBsAddStep")?.addEventListener("click", addBrowserSessionStep);
    el("bs_new_step")?.addEventListener("keydown", (e) => { if (e.key === "Enter") addBrowserSessionStep(); });
  }, 0);
}

function renderBrowserSessionRecorded(box) {
  const header = document.createElement("div");
  header.className = "flex items-center gap-2 mb-2";
  header.innerHTML = `
    <svg class="w-5 h-5 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
    <span class="text-sm font-medium" style="color:var(--text-primary);">Session recorded — ready to generate</span>`;
  box.appendChild(header);

  const info = document.createElement("p");
  info.className = "text-xs mb-3";
  info.style.color = "var(--text-tertiary)";
  info.textContent = `Session: ${browserSessionId} • ${browserSessionSteps.length} step(s)`;
  box.appendChild(info);

  renderBrowserSessionStepList(box);

  const resetBtn = document.createElement("button");
  resetBtn.type = "button";
  resetBtn.className = "mt-3 px-3 py-1.5 rounded text-xs";
  resetBtn.style.cssText = "background:var(--btn-neutral);color:var(--btn-neutral-text);border:1px solid var(--border-input);";
  resetBtn.textContent = "New recording";
  resetBtn.addEventListener("click", () => { resetBrowserSession(); renderGenParsers(); });
  box.appendChild(resetBtn);

  const hiddenInput = document.createElement("input");
  hiddenInput.type = "hidden";
  hiddenInput.id = "pf_session_id";
  hiddenInput.dataset.fieldName = "session_id";
  hiddenInput.value = browserSessionId;
  box.appendChild(hiddenInput);
}

function renderBrowserSessionStepList(box) {
  if (!browserSessionSteps.length) return;
  const list = document.createElement("div");
  list.className = "space-y-1.5 max-h-48 overflow-y-auto rounded p-2";
  list.style.cssText = "background:var(--bg-surface-alt);border:1px solid var(--border-default);";
  browserSessionSteps.forEach((s, i) => {
    const row = document.createElement("div");
    row.className = "flex items-start gap-2 text-xs";
    const statusIcon = s.status === "done"
      ? '<svg class="w-3.5 h-3.5 text-emerald-500 mt-0.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>'
      : s.status === "failed"
        ? '<svg class="w-3.5 h-3.5 text-red-500 mt-0.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>'
        : s.status === "running"
          ? '<span class="relative flex h-3.5 w-3.5 mt-0.5 shrink-0"><span class="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style="background:var(--accent);"></span><span class="relative inline-flex rounded-full h-3.5 w-3.5" style="background:var(--accent);"></span></span>'
          : '<span class="inline-flex h-3.5 w-3.5 mt-0.5 shrink-0 rounded-full" style="background:var(--border-default);"></span>';
    row.innerHTML = `${statusIcon}<span style="color:var(--text-secondary);"><strong style="color:var(--text-primary);">${i + 1}.</strong> ${escapeHtml(s.instruction || s.action_type || "Step")}</span>`;
    if (s.error) {
      const errSpan = document.createElement("span");
      errSpan.className = "text-red-500 ml-1";
      errSpan.textContent = `(${s.error})`;
      row.appendChild(errSpan);
    }
    list.appendChild(row);
  });
  box.appendChild(list);
}

async function startBrowserSession() {
  const url = el("bs_url")?.value?.trim();
  const stepsRaw = el("bs_steps")?.value?.trim();
  const browserType = el("bs_browser_type")?.value || "playwright";
  if (!url) { fieldError(el("bs_url"), "Please enter a target URL to record."); return; }

  const steps = stepsRaw ? stepsRaw.split("\n").map(s => s.trim()).filter(Boolean) : [];
  const project_id = currentProjectId;
  if (!project_id) { showToast("Please select a project before recording.", true); return; }

  const featureName = el("genModalFeatureName")?.textContent || "";

  try {
    showLoading("Starting browser session...");
    const res = await fetchJSON("/api/browser-session/start", {
      method: "POST",
      body: JSON.stringify({ project_id, url, feature_name: featureName, browser_type: browserType, steps }),
    });
    browserSessionId = res.session.id;
    browserSessionStatus = res.session.status;
    browserSessionSteps = res.session.steps || [];
    hideLoading();
    showToast("Recording started — run the agent to capture steps.");
    renderGenParsers();
    updateGenButton();
  } catch (e) {
    hideLoading();
    showToast(String(e.message || e), true);
  }
}

async function addBrowserSessionStep() {
  const input = el("bs_new_step");
  const instruction = input?.value?.trim();
  if (!instruction || !browserSessionId) return;

  try {
    const res = await fetchJSON(`/api/browser-session/${browserSessionId}/step`, {
      method: "POST",
      body: JSON.stringify({ instruction, status: "pending" }),
    });
    browserSessionSteps = res.session.steps || [];
    input.value = "";
    renderGenParsers();
  } catch (e) {
    showToast(String(e.message || e), true);
  }
}

async function completeBrowserSession() {
  if (!browserSessionId) return;
  try {
    showLoading("Completing session...");
    const res = await fetchJSON(`/api/browser-session/${browserSessionId}/complete`, {
      method: "POST",
      body: JSON.stringify({ status: "completed" }),
    });
    browserSessionStatus = res.session.status;
    browserSessionSteps = res.session.steps || [];
    hideLoading();
    showToast("Recording complete — you can now generate test cases.");
    renderGenParsers();
    updateGenButton();
  } catch (e) {
    hideLoading();
    showToast(String(e.message || e), true);
  }
}

async function cancelBrowserSession() {
  if (browserSessionId) {
    try {
      await fetchJSON(`/api/browser-session/${browserSessionId}/complete`, {
        method: "POST",
        body: JSON.stringify({ status: "failed" }),
      });
    } catch (_) {}
  }
  resetBrowserSession();
  renderGenParsers();
  updateGenButton();
}

function resetBrowserSession() {
  browserSessionId = null;
  browserSessionStatus = null;
  browserSessionSteps = [];
  bsExploreSummary = null;
  if (bsExplorePollTimer) { clearInterval(bsExplorePollTimer); bsExplorePollTimer = null; }
}

async function refreshBrowserSession() {
  if (!browserSessionId) return;
  try {
    const res = await fetchJSON(`/api/browser-session/${browserSessionId}`);
    browserSessionStatus = res.session.status;
    browserSessionSteps = res.session.steps || [];
    renderGenParsers();
    updateGenButton();
  } catch (_) {}
}

// ------------------------------------------------------------------
// AI exploration mode
// ------------------------------------------------------------------

function renderAiExploreIdleForm(box) {
  const urlWrap = document.createElement("div");
  urlWrap.innerHTML = `
    <label class="block text-xs mb-1" style="color:var(--text-tertiary);">Target URL</label>
    <input id="ai_url" type="url" class="w-full rounded px-3 py-2 text-sm" style="background:var(--bg-input);border:1px solid var(--border-input);color:var(--text-primary);" placeholder="https://example.com/signup" />`;
  box.appendChild(urlWrap);

  const goalWrap = document.createElement("div");
  goalWrap.innerHTML = `
    <label class="block text-xs mb-1" style="color:var(--text-tertiary);">Exploration goal</label>
    <textarea id="ai_goal" rows="3" class="w-full rounded px-3 py-2 text-sm" style="background:var(--bg-input);border:1px solid var(--border-input);color:var(--text-primary);" placeholder="e.g. test the signup flow with valid and invalid email"></textarea>
    <p class="text-xs mt-1" style="color:var(--text-tertiary);">Be specific. The agent stops when the goal is covered or the budget runs out.</p>`;
  box.appendChild(goalWrap);

  const optsRow = document.createElement("div");
  optsRow.className = "grid grid-cols-2 gap-3";
  optsRow.innerHTML = `
    <div>
      <label class="block text-xs mb-1" style="color:var(--text-tertiary);">Max actions</label>
      <input id="ai_max_actions" type="number" min="5" max="200" value="60" class="w-full rounded px-3 py-2 text-sm" style="background:var(--bg-input);border:1px solid var(--border-input);color:var(--text-primary);" />
    </div>
    <div>
      <label class="block text-xs mb-1" style="color:var(--text-tertiary);">Driver</label>
      <select id="ai_driver" class="w-full rounded px-3 py-2 text-sm" style="background:var(--bg-input);border:1px solid var(--border-input);color:var(--text-primary);">
        <option value="playwright">Playwright (recommended)</option>
        <option value="mcp">Browser MCP (browsermcp.io — needs extension connected)</option>
      </select>
    </div>`;
  box.appendChild(optsRow);

  const roWrap = document.createElement("label");
  roWrap.className = "flex items-center gap-2 text-xs mt-1";
  roWrap.style.color = "var(--text-secondary)";
  roWrap.innerHTML = `
    <input id="ai_read_only" type="checkbox" checked />
    <span>Read-only mode (block clicks on Delete / Pay / Send / Confirm)</span>`;
  box.appendChild(roWrap);

  const btnRow = document.createElement("div");
  btnRow.className = "flex gap-3";
  const startBtn = document.createElement("button");
  startBtn.type = "button";
  startBtn.id = "btnAiStart";
  startBtn.className = "px-4 py-2 rounded text-sm font-medium text-white";
  startBtn.style.cssText = "background:var(--accent);";
  startBtn.innerHTML = `<span class="flex items-center gap-2"><svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg> Start AI Exploration</span>`;
  startBtn.addEventListener("click", startAiExploration);
  btnRow.appendChild(startBtn);
  box.appendChild(btnRow);
}

function renderAiExploreRunning(box) {
  const s = bsExploreSummary || {};
  const header = document.createElement("div");
  header.className = "flex items-center gap-2 mb-2";
  header.innerHTML = `
    <span class="relative flex h-3 w-3"><span class="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style="background:var(--accent);"></span><span class="relative inline-flex rounded-full h-3 w-3" style="background:var(--accent);"></span></span>
    <span class="text-sm font-medium" style="color:var(--accent-text);">AI exploration in progress…</span>`;
  box.appendChild(header);

  const info = document.createElement("p");
  info.className = "text-xs mb-3";
  info.style.color = "var(--text-tertiary)";
  info.textContent = `Session: ${browserSessionId}`;
  box.appendChild(info);

  const stats = document.createElement("div");
  stats.className = "grid grid-cols-3 gap-3 text-xs";
  stats.innerHTML = `
    <div class="rounded p-2" style="background:var(--bg-surface-alt);border:1px solid var(--border-default);">
      <div style="color:var(--text-tertiary);">Actions</div>
      <div class="text-lg font-semibold" style="color:var(--text-primary);">${s.actions_count || 0}</div>
    </div>
    <div class="rounded p-2" style="background:var(--bg-surface-alt);border:1px solid var(--border-default);">
      <div style="color:var(--text-tertiary);">Pages</div>
      <div class="text-lg font-semibold" style="color:var(--text-primary);">${s.pages_count || 0}</div>
    </div>
    <div class="rounded p-2" style="background:var(--bg-surface-alt);border:1px solid var(--border-default);">
      <div style="color:var(--text-tertiary);">Errors observed</div>
      <div class="text-lg font-semibold" style="color:var(--text-primary);">${s.errors_count || 0}</div>
    </div>`;
  box.appendChild(stats);

  if (s.current_url) {
    const url = document.createElement("p");
    url.className = "text-xs mt-2";
    url.style.color = "var(--text-tertiary)";
    url.innerHTML = `Current: <span style="color:var(--text-secondary);font-family:monospace;">${escapeHtml(s.current_url)}</span>`;
    box.appendChild(url);
  }
  if (s.last_action) {
    const la = document.createElement("p");
    la.className = "text-xs";
    la.style.color = "var(--text-tertiary)";
    la.textContent = `Last action: ${s.last_action}`;
    box.appendChild(la);
  }

  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "mt-3 px-4 py-2 rounded text-sm font-medium";
  cancelBtn.style.cssText = "background:var(--btn-neutral);color:var(--btn-neutral-text);border:1px solid var(--border-input);";
  cancelBtn.textContent = "Cancel exploration";
  cancelBtn.addEventListener("click", cancelAiExploration);
  box.appendChild(cancelBtn);
}

async function startAiExploration() {
  const url = el("ai_url")?.value?.trim();
  const goal = el("ai_goal")?.value?.trim();
  const max_actions = parseInt(el("ai_max_actions")?.value || "60", 10);
  const driver = el("ai_driver")?.value || "playwright";
  const read_only = !!el("ai_read_only")?.checked;
  if (!url) { fieldError(el("ai_url"), "Please enter a target URL to explore."); return; }
  if (!goal) { fieldError(el("ai_goal"), "Please describe what the agent should explore."); return; }
  const project_id = currentProjectId;
  if (!project_id) { showToast("Please select a project before exploring.", true); return; }

  const featureName = el("genModalFeatureName")?.textContent || "";

  try {
    showLoading("Starting AI exploration...");
    // Create session shell.
    const startRes = await fetchJSON("/api/browser-session/start", {
      method: "POST",
      body: JSON.stringify({ project_id, url, feature_name: featureName, browser_type: "playwright", steps: [] }),
    });
    browserSessionId = startRes.session.id;
    browserSessionStatus = startRes.session.status;
    browserSessionSteps = [];

    // Kick off explorer.
    await fetchJSON(`/api/browser-session/${browserSessionId}/explore`, {
      method: "POST",
      body: JSON.stringify({ goal, max_actions, driver, read_only }),
    });
    bsExploreSummary = { status: "running", pages_count: 0, actions_count: 0, errors_count: 0 };
    hideLoading();
    showToast("Exploration started — the agent is interacting with the page.");
    renderGenParsers();
    updateGenButton();
    if (bsExplorePollTimer) clearInterval(bsExplorePollTimer);
    bsExplorePollTimer = setInterval(pollAiExplorationStatus, 1500);
  } catch (e) {
    hideLoading();
    showToast(String(e.message || e), true);
  }
}

async function pollAiExplorationStatus() {
  if (!browserSessionId) return;
  try {
    const s = await fetchJSON(`/api/browser-session/${browserSessionId}/explore/status`);
    bsExploreSummary = s;
    if (s.status === "done" || s.status === "error" || s.status === "cancelled") {
      if (bsExplorePollTimer) { clearInterval(bsExplorePollTimer); bsExplorePollTimer = null; }
      // Sync session state from server so the recorded view renders.
      await refreshBrowserSession();
      const note = s.status === "done"
        ? `Exploration complete. ${s.actions_count || 0} actions, ${s.pages_count || 0} pages, ${s.errors_count || 0} errors observed.`
        : s.status === "cancelled" ? "Exploration cancelled." : `Exploration ended: ${s.error || "unknown error"}`;
      showToast(note, s.status === "error");
    } else {
      renderGenParsers();
    }
  } catch (_) {
    // Transient; keep polling.
  }
}

async function cancelAiExploration() {
  if (!browserSessionId) return;
  try {
    await fetchJSON(`/api/browser-session/${browserSessionId}/explore/cancel`, { method: "POST" });
    showToast("Cancelling exploration...");
  } catch (e) {
    showToast(String(e.message || e), true);
  }
}

function renderGenMultiBlocks() {
  const container = el("genMultiInputBlocks");
  if (!container || !parsers.length) return;
  if (!multiBlocks.length) multiBlocks.push({ parserName: parsers[0].name });
  container.innerHTML = "";
  multiBlocks.forEach((block, i) => {
    const p = parsers.find(x => x.name === block.parserName) || parsers[0];
    const card = document.createElement("div");
    card.className = "rounded-lg p-3 space-y-3";
    card.style.cssText = "background:var(--bg-surface-alt);border:1px solid var(--border-default);";
    const head = document.createElement("div");
    head.className = "flex flex-wrap justify-between gap-2 items-center";
    head.innerHTML = `<span class="text-xs font-semibold uppercase" style="color:var(--text-tertiary);">Source ${i + 1}</span>`;
    if (multiBlocks.length > 1) {
      const rm = document.createElement("button");
      rm.type = "button"; rm.className = "text-xs text-red-500 hover:underline"; rm.textContent = "Remove";
      rm.addEventListener("click", () => { multiBlocks.splice(i, 1); renderGenMultiBlocks(); });
      head.appendChild(rm);
    }
    card.appendChild(head);
    const sel = document.createElement("select");
    sel.className = "w-full rounded px-3 py-2 text-sm";
    sel.style.cssText = "background:var(--bg-input);border:1px solid var(--border-input);color:var(--text-primary);";
    parsers.forEach(opt => {
      const o = document.createElement("option");
      o.value = opt.name; o.textContent = opt.display_name;
      if (opt.name === p.name) o.selected = true;
      sel.appendChild(o);
    });
    sel.addEventListener("change", () => { multiBlocks[i].parserName = sel.value; renderGenMultiBlocks(); });
    card.appendChild(sel);
    (p.input_fields || []).forEach(f => {
      const w = fieldInputMulti(f, i);
      if (f.name === "feature_name") w.classList.add("hidden");
      card.appendChild(w);
    });
    if (p.accepts_file) {
      const wrap = document.createElement("div");
      const labf = document.createElement("label");
      labf.className = "block text-xs mb-1";
      labf.style.color = "var(--text-tertiary)";
      labf.textContent = `Screenshot ${i + 1}`;
      const zone = createImageDropZone({ id: `multi_file_${i}` });
      wrap.appendChild(labf); wrap.appendChild(zone); card.appendChild(wrap);
    }
    container.appendChild(card);
  });
}

function collectParserData() {
  const p = parsers.find(x => x.name === activeParser);
  if (!p) return {};
  const data = {};
  (p.input_fields || []).forEach(f => {
    const inp = document.getElementById(`pf_${f.name}`);
    if (inp) data[f.name] = readFieldValue(inp);
  });
  return data;
}

function collectMultiInputs() {
  const inputs = []; const files = [];
  for (let i = 0; i < multiBlocks.length; i++) {
    const pname = multiBlocks[i].parserName;
    const p = parsers.find(x => x.name === pname);
    if (!p) continue;
    const data = {};
    (p.input_fields || []).forEach(f => { const inp = document.getElementById(`pf_m_${i}_${f.name}`); if (inp) data[f.name] = readFieldValue(inp); });
    const item = { input_type: pname, data };
    if (p.accepts_file) {
      const fin = document.getElementById(`multi_file_${i}`);
      const file = fin?.files?.[0];
      if (!file) throw new Error(`Source ${i + 1} (${p.display_name || pname}) requires an image file.`);
      item.file_index = files.length; files.push(file);
    }
    inputs.push(item);
  }
  return { inputs, files };
}

async function loadParsers() {
  try {
    const res = await fetchJSON("/api/parsers");
    // browser_session is hidden from the UI for now; backend code stays in place.
    parsers = (Array.isArray(res?.parsers) ? res.parsers : [])
      .filter(p => p.name !== "browser_session");
    activeParser = parsers[0]?.name || null;
  } catch (e) { parsers = []; activeParser = null; console.error("loadParsers failed:", e); }
  multiBlocks = [{ parserName: activeParser || parsers[0]?.name || "text" }];
}

function updateGenButton() {
  const btn = el("btnGenerateSubmit");
  if (!btn) return;
  const fid = el("genModalFeatureId")?.value;
  const multiOn = el("genMultiInputMode")?.checked;

  if (activeParser === "browser_session") {
    btn.disabled = !currentProjectId || !fid || !browserSessionId || browserSessionStatus !== "completed";
    return;
  }
  btn.disabled = !currentProjectId || !fid || !parsers.length || (multiOn ? !multiBlocks.length : !activeParser);
}

// ---------------------------------------------------------------------------
// Generate modal
// ---------------------------------------------------------------------------
function openGenerateModal(featureId, featureName) {
  el("genModalFeatureId").value = featureId;
  el("genModalFeatureName").textContent = featureName;
  el("genLlmProvider").value = "";
  el("genLlmModel").value = "";
  el("genMinTestCases").value = "";
  genPrefTypesWidget?.reset();
  el("genMultiInputMode").checked = false;
  el("genSingleInputWrap")?.classList.remove("hidden");
  el("genMultiInputWrap")?.classList.add("hidden");
  resetBrowserSession();
  renderGenParsers();
  el("generateModal")?.classList.remove("hidden");
  updateGenButton();
}

async function submitGenerate() {
  const project_id = currentProjectId;
  const feature_id = el("genModalFeatureId").value;
  if (!project_id || !feature_id) return;
  const llm = el("genLlmProvider").value;
  const llmModel = el("genLlmModel").value.trim();
  const minRaw = el("genMinTestCases")?.value;
  let min_test_cases = null;
  if (minRaw) { const n = parseInt(minRaw, 10); if (!Number.isNaN(n) && n > 0) min_test_cases = n; }
  const preferred_test_types = genPrefTypesWidget?.getValues() || [];
  const multiOn = el("genMultiInputMode")?.checked;

  genAbortController = new AbortController();
  const usesFigma = multiOn
    ? multiBlocks.some(b => b.parserName === "figma")
    : activeParser === "figma";
  if (usesFigma) {
    showLoading("Fetching Figma file...", [
      { at: 0,  msg: "Fetching Figma file..." },
      { at: 8,  msg: "Walking design tree..." },
      { at: 15, msg: "Rendering frames..." },
      { at: 35, msg: "Analyzing frames with vision..." },
      { at: 70, msg: "Generating test cases..." },
    ]);
  } else {
    showLoading("Generating test cases...");
  }
  try {
    let result;
    if (multiOn) {
      let inputsPayload, files;
      try { ({ inputs: inputsPayload, files } = collectMultiInputs()); }
      catch (err) { hideLoading(); showToast(err.message || err, true); return; }
      if (files.length > 0) {
        const fd = new FormData();
        fd.append("inputs", JSON.stringify(inputsPayload));
        fd.append("project_id", project_id); fd.append("feature_id", feature_id);
        if (llm) fd.append("llm_provider", llm);
        if (llmModel) fd.append("llm_model", llmModel);
        if (min_test_cases) fd.append("min_test_cases", String(min_test_cases));
        if (preferred_test_types?.length) fd.append("preferred_test_types", preferred_test_types.join(","));
        files.forEach(f => fd.append("files", f));
        const r = await fetch(API + "/api/generate", { method: "POST", headers: authHeaders(), body: fd, signal: genAbortController.signal });
        const text = await r.text(); result = parseResponseBody(text);
        if (!r.ok) throw new Error(formatApiError(r.status, result));
      } else {
        const body = { inputs: inputsPayload, project_id, feature_id };
        if (llm) body.llm_provider = llm; if (llmModel) body.llm_model = llmModel;
        if (min_test_cases) body.min_test_cases = min_test_cases;
        if (preferred_test_types?.length) body.preferred_test_types = preferred_test_types;
        result = await fetchJSON("/api/generate", { method: "POST", body: JSON.stringify(body), signal: genAbortController.signal });
      }
    } else {
      const data = collectParserData();
      const p = parsers.find(x => x.name === activeParser);
      if (p?.accepts_file) {
        const fileInput = document.getElementById("parser_file");
        const file = fileInput?.files?.[0];
        if (!file) { hideLoading(); showToast("Please select an image file to upload.", true); return; }
        const fd = new FormData();
        fd.append("input_type", activeParser); fd.append("project_id", project_id); fd.append("feature_id", feature_id);
        fd.append("data", JSON.stringify(data));
        if (llm) fd.append("llm_provider", llm); if (llmModel) fd.append("llm_model", llmModel);
        if (min_test_cases) fd.append("min_test_cases", String(min_test_cases));
        if (preferred_test_types?.length) fd.append("preferred_test_types", preferred_test_types.join(","));
        fd.append("file", file);
        const r = await fetch(API + "/api/generate", { method: "POST", headers: authHeaders(), body: fd, signal: genAbortController.signal });
        const text = await r.text(); result = parseResponseBody(text);
        if (!r.ok) throw new Error(formatApiError(r.status, result));
      } else {
        const body = { input_type: activeParser, project_id, feature_id, data };
        if (llm) body.llm_provider = llm; if (llmModel) body.llm_model = llmModel;
        if (min_test_cases) body.min_test_cases = min_test_cases;
        if (preferred_test_types?.length) body.preferred_test_types = preferred_test_types;
        result = await fetchJSON("/api/generate", { method: "POST", body: JSON.stringify(body), signal: genAbortController.signal });
      }
    }
    el("generateModal")?.classList.add("hidden");
    expandedFeatures.add(feature_id);
    await loadProjectWorkspaceData();
    await refreshProjects();
    showToast(`Added ${result?.added_count || 0} test case(s); ${result?.skipped_duplicate_count || 0} duplicate(s) skipped.`);
  } catch (e) {
    if (e.name === "AbortError") showToast("Generation cancelled.", true);
    else showToast(String(e.message || e), true);
  } finally { hideLoading(); genAbortController = null; }
}

// ---------------------------------------------------------------------------
// Iterate modal
// ---------------------------------------------------------------------------
function openIterateModal(featureId, featureName) {
  el("iterModalFeatureId").value = featureId;
  el("iterModalFeatureName").textContent = featureName;
  el("iterInstruction").value = "";
  el("iterMinCases").value = "";
  iterPrefTypesWidget?.reset();
  el("iterLlmProvider").value = "";
  el("iterLlmModel").value = "";
  el("iterateModal")?.classList.remove("hidden");
}

async function submitIterate() {
  const project_id = currentProjectId;
  const feature_id = el("iterModalFeatureId").value;
  const instruction = el("iterInstruction").value.trim();
  if (!project_id) { showToast("Please select a project first.", true); return; }
  if (!instruction) { fieldError(el("iterInstruction"), "Please describe what to iterate on."); return; }
  const llm = el("iterLlmProvider").value;
  const llmModel = el("iterLlmModel").value.trim();
  const minRaw = el("iterMinCases")?.value;
  let min_test_cases = null;
  if (minRaw) { const n = parseInt(minRaw, 10); if (!Number.isNaN(n) && n > 0) min_test_cases = n; }
  const preferred_test_types = iterPrefTypesWidget?.getValues() || [];
  const body = {
    project_id, instruction, feature_id: feature_id || null,
    type_filter: null,
  };
  if (llm) body.llm_provider = llm;
  if (llmModel) body.llm_model = llmModel;
  if (min_test_cases) body.min_test_cases = min_test_cases;
  if (preferred_test_types?.length) body.preferred_test_types = preferred_test_types;

  genAbortController = new AbortController();
  showLoading("Iterating...");
  try {
    const result = await fetchJSON("/api/generate/iterate", { method: "POST", body: JSON.stringify(body), signal: genAbortController.signal });
    el("iterateModal")?.classList.add("hidden");
    if (feature_id) expandedFeatures.add(feature_id);
    await loadProjectWorkspaceData();
    await refreshProjects();
    showToast(`Added ${result?.added_count || 0} test case(s); ${result?.skipped_duplicate_count || 0} duplicate(s) skipped.`);
  } catch (e) {
    if (e.name === "AbortError") showToast("Generation cancelled.", true);
    else showToast(String(e.message || e), true);
  } finally { hideLoading(); genAbortController = null; }
}

// ---------------------------------------------------------------------------
// Export modal
// ---------------------------------------------------------------------------
function openExportModal() {
  const container = el("exportFeatureCheckboxes");
  if (!container) return;
  container.innerHTML = `<label class="flex items-center gap-2 text-sm cursor-pointer px-1 py-0.5" style="color:var(--text-secondary);">
    <input type="checkbox" id="exportFeatureAll" checked class="shrink-0" /> <span>All features</span>
  </label>`;
  lastLoadedFeatures.forEach(f => {
    const label = document.createElement("label");
    label.className = "flex items-center gap-2 text-sm cursor-pointer px-1 py-0.5";
    label.style.color = "var(--text-secondary)";
    label.innerHTML = `<input type="checkbox" class="export-feat-cb shrink-0" value="${escapeHtml(f.id)}" checked /> <span>${escapeHtml(f.name)} (${(lastLoadedCases.filter(tc => tc.feature_id === f.id)).length})</span>`;
    container.appendChild(label);
  });
  el("exportFeatureAll")?.addEventListener("change", (e) => {
    container.querySelectorAll(".export-feat-cb").forEach(cb => { cb.checked = e.target.checked; });
  });
  el("exportSearchTerm").value = "";
  el("exportPriority").value = "";
  el("exportFormat").value = "excel";
  el("exportModal")?.classList.remove("hidden");
}

async function submitExport() {
  if (!currentProjectId) return;
  const allChecked = el("exportFeatureAll")?.checked;
  let featureIds = [];
  if (!allChecked) {
    el("exportFeatureCheckboxes")?.querySelectorAll(".export-feat-cb:checked").forEach(cb => featureIds.push(cb.value));
  }
  const search = el("exportSearchTerm")?.value?.trim() || "";
  const priority = el("exportPriority")?.value || "";
  const fmt = el("exportFormat")?.value || "excel";
  const ext = fmt === "excel" ? "xlsx" : fmt === "json" ? "json" : "csv";

  let url = `/api/export/${currentProjectId}?format=${encodeURIComponent(fmt)}`;
  if (featureIds.length > 0 && !allChecked) url += `&feature_ids=${encodeURIComponent(featureIds.join(","))}`;
  if (search) url += `&search=${encodeURIComponent(search)}`;
  if (priority) url += `&priority=${encodeURIComponent(priority)}`;

  try {
    await downloadBlob(url, `test_cases_export.${ext}`);
    el("exportModal")?.classList.add("hidden");
    showToast("Export downloaded successfully.");
  } catch (e) { showToast(String(e.message || e), true); }
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------
// Bento (typeui.sh) chart palette — warm, muted, theme-cohesive.
// Same set works on both cream (light) and warm-brown (dark) surfaces because
// colours are mid-saturation and mid-luminance.
const DASH_COLORS = [
  '#80A1C1',  // Bento secondary — muted blue
  '#F4A78A',  // warm coral peach (Bento primary, slightly punched up for chart visibility)
  '#A8C49E',  // sage green
  '#E8B670',  // soft amber
  '#D88FA8',  // dusty rose
  '#A89BD4',  // soft lavender
  '#7AB5A7',  // muted teal
  '#C9A876',  // golden tan
  '#B89AC4',  // soft violet
  '#88B4D4',  // light blue
];
// Bento semantic status — exact alignment with --status-* CSS tokens.
const DASH_PRIORITY_COLORS = { high: '#DC2626', medium: '#D97706', low: '#16A34A' };
// History timeline source markers — Bento-tinted variants of the chart palette.
const DASH_SOURCE_COLORS = { figma: '#A89BD4', jira: '#80A1C1', text: '#A9978A', screenshot: '#A8C49E' };
let _dashCharts = [];

function _destroyDashCharts() {
  _dashCharts.forEach(c => { try { c.destroy(); } catch (_) {} });
  _dashCharts = [];
}

function _dashBuildLegend(container, data, total) {
  if (!container) return;
  container.innerHTML = data.map(d =>
    `<span class="dash-legend-item"><span class="dash-legend-dot" style="background:${d.color}"></span>${escapeHtml(d.label)} ${total ? Math.round(d.value / total * 100) + '%' : ''}</span>`
  ).join('');
}

function _dashMakeDoughnut(canvasId, data, total) {
  const cvs = document.getElementById(canvasId);
  if (!cvs || !data.length) return null;
  return new Chart(cvs, {
    type: 'doughnut',
    data: {
      labels: data.map(d => d.label),
      datasets: [{
        data: data.map(d => d.value),
        backgroundColor: data.map(d => d.color),
        borderWidth: 2, borderColor: getComputedStyle(document.documentElement).getPropertyValue('--chart-border').trim() || '#fff', hoverOffset: 6
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false, cutout: '62%',
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => ` ${ctx.label}: ${ctx.raw} (${Math.round(ctx.raw / total * 100)}%)` } }
      }
    }
  });
}

function _dashBuildBars(containerId, data, maxVal) {
  const box = document.getElementById(containerId);
  if (!box) return;
  if (!data.length) { box.innerHTML = '<p class="text-sm" style="color:var(--text-tertiary);">No data yet.</p>'; return; }
  box.innerHTML = data.map(d => `
    <div class="dash-bar-row">
      <span class="dash-bar-label">${escapeHtml(d.label)}</span>
      <div class="dash-bar-track">
        <div class="dash-bar-fill" style="width:${Math.round(d.value / maxVal * 100)}%;background:${d.color};"></div>
      </div>
      <span class="dash-bar-count">${d.value}</span>
    </div>`).join('');
}

async function loadDashboard() {
  const pid = currentProjectId;
  const empty = el("dashboardEmpty");
  const content = el("dashboardContent");
  if (!pid) { empty?.classList.remove("hidden"); content?.classList.add("hidden"); return; }
  try {
    const [stats, hist] = await Promise.all([
      fetchJSON(`/api/projects/${pid}/stats`),
      fetchJSON(`/api/projects/${pid}/input-history?limit=50`),
    ]);
    empty?.classList.add("hidden"); content?.classList.remove("hidden");
    _destroyDashCharts();

    const total = stats.total ?? 0;
    const featureCount = (stats.by_feature || []).length;
    const highCount = (stats.by_priority || {}).high || 0;
    const medCount = (stats.by_priority || {}).medium || 0;
    const lowCount = (stats.by_priority || {}).low || 0;


    // Metric cards
    el("dashTotal").textContent = String(total);
    const detailEl = el("dashTotalDetail");
    if (detailEl) detailEl.textContent = total ? `across ${featureCount} feature${featureCount !== 1 ? 's' : ''}` : 'no tests yet';

    el("dashHighVal").textContent = String(highCount);
    const highDetail = el("dashHighDetail");
    if (highDetail) highDetail.textContent = total ? `${Math.round(highCount / total * 100)}% of total` : '';

    el("dashMedVal").textContent = String(medCount);
    const medDetail = el("dashMedDetail");
    if (medDetail) medDetail.textContent = total ? `${Math.round(medCount / total * 100)}% of total` : '';

    const lastRun = (hist || [])[0];
    const lrVal = el("dashLastRunVal");
    const lrDetail = el("dashLastRunDetail");
    if (lrVal) {
      if (lastRun?.at) {
        lrVal.textContent = new Date(lastRun.at).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", month: 'short', day: 'numeric' });
      } else { lrVal.innerHTML = '&mdash;'; }
    }
    if (lrDetail) lrDetail.textContent = lastRun ? `via ${lastRun.source_type}` : '';

    // Prepare chart data
    const typeEntries = Object.entries(stats.by_type || {}).sort((a, b) => b[1] - a[1]);
    const typeData = typeEntries.map(([k, v], i) => ({ label: k, value: v, color: DASH_COLORS[i % DASH_COLORS.length] }));

    const featEntries = (stats.by_feature || []).sort((a, b) => b.count - a.count);
    const featData = featEntries.map((f, i) => ({ label: f.name, value: f.count, color: DASH_COLORS[i % DASH_COLORS.length] }));

    // Doughnut: by type
    _dashBuildLegend(el("dashTypeLegend"), typeData, total);
    const c1 = _dashMakeDoughnut("dashTypeChart", typeData, total);
    if (c1) _dashCharts.push(c1);

    // Doughnut: by feature
    _dashBuildLegend(el("dashFeatLegend"), featData, total);
    const c2 = _dashMakeDoughnut("dashFeatChart", featData, total);
    if (c2) _dashCharts.push(c2);

    // Bar tracks: by type
    const typeMax = typeEntries.length ? Math.max(...typeEntries.map(([, v]) => v), 1) : 1;
    _dashBuildBars("dashTypeBars", typeData, typeMax);

    // Bar tracks: by feature
    const featMax = featEntries.length ? Math.max(...featEntries.map(f => f.count), 1) : 1;
    _dashBuildBars("dashFeatBars", featData, featMax);

    // Priority breakdown: pills + horizontal bar chart
    const pillBox = el("dashPriorityPills");
    if (pillBox) {
      pillBox.innerHTML = '';
      if (highCount) pillBox.innerHTML += `<span class="dash-pill-high">High: ${highCount}</span>`;
      if (medCount) pillBox.innerHTML += `<span class="dash-pill-med">Medium: ${medCount}</span>`;
      if (lowCount) pillBox.innerHTML += `<span class="dash-pill-low">Low: ${lowCount}</span>`;
    }
    const prioCanvas = document.getElementById("dashPriorityChart");
    if (prioCanvas) {
      const priLabels = []; const priValues = []; const priBg = [];
      if (highCount) { priLabels.push('High'); priValues.push(highCount); priBg.push(DASH_PRIORITY_COLORS.high); }
      if (medCount) { priLabels.push('Medium'); priValues.push(medCount); priBg.push(DASH_PRIORITY_COLORS.medium); }
      if (lowCount) { priLabels.push('Low'); priValues.push(lowCount); priBg.push(DASH_PRIORITY_COLORS.low); }
      if (priLabels.length) {
        const c3 = new Chart(prioCanvas, {
          type: 'bar',
          data: { labels: priLabels, datasets: [{ data: priValues, backgroundColor: priBg, borderRadius: 4, borderSkipped: false }] },
          options: {
            indexAxis: 'y', responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => ` ${ctx.raw} tests` } } },
            scales: {
              x: { display: false, max: Math.max(...priValues) + 5 },
              y: { grid: { display: false }, border: { display: false }, ticks: { font: { size: 13 }, color: getComputedStyle(document.documentElement).getPropertyValue('--chart-tick').trim() || '#888' } }
            }
          }
        });
        _dashCharts.push(c3);
      }
    }

  } catch { empty?.classList.remove("hidden"); content?.classList.add("hidden"); }
}

// ---------------------------------------------------------------------------
// Auth UI
// ---------------------------------------------------------------------------
async function refreshUserChip() {
  const chip = el("userChip"); const btn = el("btnLogout");
  const tok = getToken();
  if (!chip || !btn) return;
  if (!tok) { chip.classList.add("hidden"); btn.classList.add("hidden"); chip.textContent = ""; return; }
  const expHint = formatTokenExpiry(tok);
  try {
    const u = await fetchJSON("/api/auth/me");
    chip.textContent = `${u.name || "User"} (${u.email})${expHint}`;
    chip.classList.remove("hidden"); btn.classList.remove("hidden");
  } catch { chip.textContent = `Signed in${expHint}`; chip.classList.remove("hidden"); btn.classList.remove("hidden"); }
}

async function tryRegister() {
  try {
    const res = await fetchJSON("/api/auth/register", {
      method: "POST", body: JSON.stringify({ email: el("authEmail").value.trim(), password: el("authPassword").value, name: "User" }),
    });
    setToken(res.access_token); el("authSection").classList.add("hidden");
    await refreshProjects(); await refreshUserChip(); await handleRoute().catch(() => {});
  } catch (e) { showToast(String(e.message || e), true); }
}

async function tryLogin() {
  try {
    const res = await fetchJSON("/api/auth/login", {
      method: "POST", body: JSON.stringify({ email: el("authEmail").value.trim(), password: el("authPassword").value }),
    });
    setToken(res.access_token); el("authSection").classList.add("hidden");
    await refreshProjects(); await refreshUserChip(); await handleRoute().catch(() => {});
  } catch (e) { showToast(String(e.message || e), true); }
}

function logout() {
  setToken(null); selectedTcIds.clear(); refreshUserChip();
  el("authSection")?.classList.remove("hidden");
}

// ---------------------------------------------------------------------------
// Settings / API keys
// ---------------------------------------------------------------------------
const LLM_PROVIDER_TO_KEY = { openai: "openai_api_key", anthropic: "anthropic_api_key", gemini: "gemini_api_key" };
const LLM_PROVIDER_LABELS = { openai: "OpenAI API key", anthropic: "Anthropic API key", gemini: "Google Gemini API key" };
const DEFAULT_SECRET_PLACEHOLDER = {
  key_llm_api_key: "Paste new key (optional)",
  inline_key_llm_api_key: "Paste new key (optional)",
  key_figma_access_token: "New token (optional)",
  inline_key_figma_access_token: "New token (optional)",
  key_jira_base_url: "https://your.atlassian.net",
  inline_key_jira_base_url: "https://your.atlassian.net",
  key_jira_email: "account@company.com",
  inline_key_jira_email: "account@company.com",
  key_jira_api_token: "New token (optional)",
  inline_key_jira_api_token: "New token (optional)",
};
let lastApiKeysPayload = null;

function applySecretFieldHintAndPlaceholder(inputId, hintId, info) {
  const hint = hintId ? el(hintId) : null;
  const inp = inputId ? el(inputId) : null;
  const defPh = DEFAULT_SECRET_PLACEHOLDER[inputId] || "";
  if (!info) return;
  if (hint) {
    if (!info.configured) {
      hint.textContent = "Not set";
      hint.className = "text-xs text-gray-400 mt-1";
    } else {
      hint.textContent = `Saved: ${info.masked}`;
      hint.className = "text-xs text-emerald-600 mt-1";
    }
  }
  if (inp) {
    inp.placeholder = info.configured && info.masked ? info.masked : defPh;
  }
}

async function clearApiKey(name) {
  if (!(await customConfirm(
    `The stored override for "${name}" will be removed from the local database.`,
    { title: "Clear stored API key?", okLabel: "Clear" }
  ))) return;
  try {
    await fetchJSON("/api/settings/keys", { method: "PUT", body: JSON.stringify({ [name]: "" }) });
    showToast("API key cleared."); await loadInlineSettings();
  } catch (e) { showToast(String(e.message || e), true); }
}

async function loadFigmaCacheStatus() {
  // Best-effort: refresh the "X cached" hint in both Settings views.
  try {
    const res = await fetchJSON("/api/settings/figma-cache");
    const n = res?.count ?? 0;
    const msg = n > 0
      ? `${n} design${n === 1 ? "" : "s"} cached. Speeds up repeat generations from the same URL.`
      : "Nothing cached yet. First generation from a design will populate this.";
    const a = el("figmaCacheHint"); if (a) a.textContent = msg;
    const b = el("inlineFigmaCacheHint"); if (b) b.textContent = msg;
  } catch (e) { /* non-fatal */ }
}

async function clearFigmaCache() {
  if (!(await customConfirm(
    "All cached Figma designs will be removed. The next generation from each design will re-fetch from Figma.",
    { title: "Clear Figma cache?", okLabel: "Clear Cache" }
  ))) return;
  try {
    showLoading("Clearing Figma cache...");
    await fetchJSON("/api/settings/figma-cache", { method: "DELETE", body: JSON.stringify({}) });
    showToast("Figma cache cleared.");
    await loadFigmaCacheStatus();
  } catch (e) { showToast(String(e.message || e), true); }
  finally { hideLoading(); }
}

async function testFigmaToken(inputId) {
  // Use whatever's typed in the input; fall back to the saved token on the server.
  const typed = (el(inputId)?.value || "").trim();
  try {
    showLoading("Testing Figma token...");
    const res = await fetchJSON("/api/settings/test-figma", {
      method: "POST",
      body: JSON.stringify(typed ? { token: typed } : {}),
    });
    const who = res.email || res.handle || "ok";
    showToast(`Figma token verified — connected as ${who}.`);
  } catch (e) {
    showToast(String(e.message || e), true);
  } finally {
    hideLoading();
  }
}

// ---------------------------------------------------------------------------
// Integrations (Figma + Atlassian) — connected/not-connected cards
// ---------------------------------------------------------------------------
let atlassianEditing = false;

function integrationConnectedBadge() {
  return `<span class="text-xs inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full" style="background:var(--accent-subtle);color:var(--accent-text);"><span style="width:7px;height:7px;border-radius:9999px;background:var(--accent-text);display:inline-block;"></span>Connected</span>`;
}
function _roLine(label, masked) {
  return `<div class="flex justify-between gap-3"><span class="text-xs shrink-0" style="color:var(--text-tertiary);">${escapeHtml(label)}</span><span class="text-sm font-mono break-all text-right" style="color:var(--text-primary);">${escapeHtml(masked || "")}</span></div>`;
}
function _inputLine(label, id, type, placeholder) {
  return `<div><label class="text-xs" style="color:var(--text-tertiary);">${escapeHtml(label)}</label><input id="${id}" type="${type}" autocomplete="off" class="w-full mt-1 rounded px-3 py-2 text-sm" style="background:var(--bg-input);border:1px solid var(--border-input);color:var(--text-primary);" placeholder="${escapeHtml(placeholder)}" /></div>`;
}

function _figmaCardHtml(keys, configured) {
  const masked = keys.figma_access_token?.masked || "";
  const body = configured
    ? `<div class="flex justify-between items-center gap-2">
         <div class="min-w-0"><div class="text-xs" style="color:var(--text-tertiary);">Access token</div><div class="text-sm font-mono break-all" style="color:var(--text-primary);">${escapeHtml(masked)}</div></div>
         <div class="flex items-center gap-2 shrink-0">
           <button type="button" data-test-figma-input="inline_key_figma_access_token" class="text-sm px-3 py-1.5 rounded" style="background:var(--btn-neutral);color:var(--btn-neutral-text);border:1px solid var(--border-input);">Test</button>
           <button type="button" data-action="figma-disconnect" class="text-sm px-3 py-1.5 rounded font-medium" style="background:transparent;color:var(--status-high);border:1px solid var(--status-high);">Disconnect</button>
         </div>
       </div>`
    : `<div>
         <label class="text-xs" style="color:var(--text-tertiary);">Access token</label>
         <input id="inline_key_figma_access_token" type="password" autocomplete="off" class="w-full mt-1 rounded px-3 py-2 text-sm" style="background:var(--bg-input);border:1px solid var(--border-input);color:var(--text-primary);" placeholder="Paste access token" />
         <p id="figmaConnectError" class="text-xs mt-1" style="color:var(--status-high);"></p>
         <div class="flex items-center gap-2 mt-2">
           <button type="button" data-test-figma-input="inline_key_figma_access_token" class="text-sm px-3 py-1.5 rounded" style="background:var(--btn-neutral);color:var(--btn-neutral-text);border:1px solid var(--border-input);">Test</button>
           <button type="button" data-action="figma-connect" class="text-sm px-3 py-1.5 rounded font-medium" style="background:var(--accent);color:var(--text-on-accent);">Connect</button>
         </div>
       </div>`;
  return `<div class="rounded-xl p-4 shadow-sm" style="background:var(--bg-surface);border:1px solid var(--border-default);">
      <div class="flex items-center justify-between gap-2 mb-3"><h3 class="text-base font-semibold">Figma</h3>${configured ? integrationConnectedBadge() : ""}</div>
      ${body}
      <div class="flex justify-between items-center gap-2 pt-3 mt-3" style="border-top:1px solid var(--border-default);">
        <div class="min-w-0 flex items-center gap-2">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="width:16px;height:16px;flex-shrink:0;color:var(--text-muted);"><ellipse cx="12" cy="6" rx="8" ry="3"/><path stroke-linecap="round" d="M4 6v6c0 1.66 3.58 3 8 3s8-1.34 8-3V6M4 12v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6"/></svg>
          <p id="inlineFigmaCacheHint" class="text-xs" style="color:var(--text-muted);"></p>
        </div>
        <button type="button" data-action="figma-clear-cache" class="text-sm px-3 py-1.5 rounded shrink-0" style="background:var(--btn-neutral);color:var(--btn-neutral-text);border:1px solid var(--border-input);">Clear cache</button>
      </div>
    </div>`;
}

function _atlassianCardHtml(keys, configured) {
  const editingOrNew = !configured || atlassianEditing;
  const rows = !editingOrNew
    ? `${_roLine("Site URL", keys.jira_base_url?.masked)}${_roLine("Account email", keys.jira_email?.masked)}${_roLine("API token", keys.jira_api_token?.masked)}`
    : `${_inputLine("Site URL", "inline_key_jira_base_url", "url", keys.jira_base_url?.masked || "https://your.atlassian.net")}
       ${_inputLine("Account email", "inline_key_jira_email", "email", keys.jira_email?.masked || "account@company.com")}
       ${_inputLine("API token", "inline_key_jira_api_token", "password", keys.jira_api_token?.masked || "API token")}
       ${atlassianEditing ? `<p class="text-xs" style="color:var(--text-tertiary);">Editing replaces all three Atlassian fields. Re-enter every value.</p>` : ""}
       <p id="atlassianError" class="text-xs" style="color:var(--status-high);"></p>`;
  const actions = !editingOrNew
    ? `<button type="button" data-action="atlassian-edit" class="text-sm px-3 py-1.5 rounded" style="background:var(--btn-neutral);color:var(--btn-neutral-text);border:1px solid var(--border-input);">Edit</button>
       <button type="button" data-action="atlassian-disconnect" class="text-sm px-3 py-1.5 rounded font-medium" style="background:transparent;color:var(--status-high);border:1px solid var(--status-high);">Disconnect</button>`
    : (atlassianEditing
      ? `<button type="button" data-action="atlassian-cancel" class="text-sm px-3 py-1.5 rounded" style="background:var(--btn-neutral);color:var(--btn-neutral-text);border:1px solid var(--border-input);">Cancel</button>
         <button type="button" data-action="atlassian-save" class="text-sm px-3 py-1.5 rounded font-medium" style="background:var(--accent);color:var(--text-on-accent);">Save</button>`
      : `<button type="button" data-action="atlassian-save" class="text-sm px-3 py-1.5 rounded font-medium" style="background:var(--accent);color:var(--text-on-accent);">Connect</button>`);
  return `<div class="rounded-xl p-4 shadow-sm" style="background:var(--bg-surface);border:1px solid var(--border-default);">
      <div class="flex items-center justify-between gap-2">
        <div><h3 class="text-base font-semibold">Atlassian</h3><p class="text-xs" style="color:var(--text-tertiary);">Connects Jira issues</p></div>
        ${!editingOrNew ? integrationConnectedBadge() : ""}
      </div>
      <div class="space-y-2 mt-3">${rows}</div>
      <div class="flex items-center gap-2 mt-3">${actions}</div>
    </div>`;
}

function renderIntegrations() {
  const container = el("integrationsContainer");
  if (!container) return;
  const keys = lastApiKeysPayload?.keys || {};
  const figmaConfigured = !!keys.figma_access_token?.configured;
  const jiraConfigured = !!(keys.jira_base_url?.configured && keys.jira_email?.configured && keys.jira_api_token?.configured);
  container.innerHTML = _figmaCardHtml(keys, figmaConfigured) + _atlassianCardHtml(keys, jiraConfigured);
  loadFigmaCacheStatus();
}

async function figmaConnect() {
  const v = (el("inline_key_figma_access_token")?.value || "").trim();
  const err = el("figmaConnectError");
  if (!v) { if (err) err.textContent = "Enter a token to connect."; return; }
  try {
    showLoading("Saving...");
    await fetchJSON("/api/settings/keys", { method: "PUT", body: JSON.stringify({ figma_access_token: v }) });
    await loadInlineSettings();
    showToast("Figma connected.");
  } catch (e) { showToast(String(e.message || e), true); }
  finally { hideLoading(); }
}

async function figmaDisconnect() {
  if (!(await customConfirm("The stored Figma access token will be removed.", { title: "Disconnect Figma?", okLabel: "Disconnect" }))) return;
  try {
    showLoading("Disconnecting...");
    await fetchJSON("/api/settings/keys", { method: "PUT", body: JSON.stringify({ figma_access_token: "" }) });
    await loadInlineSettings();
    showToast("Figma disconnected.");
  } catch (e) { showToast(String(e.message || e), true); }
  finally { hideLoading(); }
}

async function atlassianSave() {
  const base = (el("inline_key_jira_base_url")?.value || "").trim();
  const email = (el("inline_key_jira_email")?.value || "").trim();
  const token = (el("inline_key_jira_api_token")?.value || "").trim();
  const err = el("atlassianError");
  if (err) err.textContent = "";
  if (!base || !email || !token) { if (err) err.textContent = "All three Atlassian fields are required."; return; }
  if (!/^https?:\/\//.test(base)) { if (err) err.textContent = "Site URL must start with http:// or https://"; return; }
  try {
    showLoading("Saving...");
    await fetchJSON("/api/settings/keys", { method: "PUT", body: JSON.stringify({ jira_base_url: base, jira_email: email, jira_api_token: token }) });
    atlassianEditing = false;
    await loadInlineSettings();
    showToast("Atlassian connected.");
  } catch (e) { showToast(String(e.message || e), true); }
  finally { hideLoading(); }
}

async function atlassianDisconnect() {
  if (!(await customConfirm("The stored Site URL, account email, and API token will be removed.", { title: "Disconnect Atlassian?", okLabel: "Disconnect" }))) return;
  try {
    showLoading("Disconnecting...");
    await fetchJSON("/api/settings/keys", { method: "PUT", body: JSON.stringify({ jira_base_url: "", jira_email: "", jira_api_token: "" }) });
    atlassianEditing = false;
    await loadInlineSettings();
    showToast("Atlassian disconnected.");
  } catch (e) { showToast(String(e.message || e), true); }
  finally { hideLoading(); }
}

// Inline settings (sidebar view)
function switchInlineSettingsTab(which) {
  el("settingsInlinePanelLlm")?.classList.toggle("hidden", which !== "llm");
  el("settingsInlinePanelIntegrations")?.classList.toggle("hidden", which !== "integrations");
  el("inlineSettingsFooter")?.classList.toggle("hidden", which !== "llm");
  const tLlm = el("settingsInlineTabLlm"); const tInt = el("settingsInlineTabIntegrations");
  if (tLlm) { tLlm.style.borderBottom = which === "llm" ? "2px solid var(--accent)" : "2px solid transparent"; tLlm.style.color = which === "llm" ? "var(--accent-text)" : "var(--text-tertiary)"; }
  if (tInt) { tInt.style.borderBottom = which !== "llm" ? "2px solid var(--accent)" : "2px solid transparent"; tInt.style.color = which !== "llm" ? "var(--accent-text)" : "var(--text-tertiary)"; }
  if (which !== "llm") renderIntegrations();
}
function inlineSelectedLlmBackendKey() { return LLM_PROVIDER_TO_KEY[el("inlineLlmApiKeyProvider")?.value || "openai"] || "openai_api_key"; }
function applyInlineLlmKeyHint(keysData) {
  const label = el("inlineLabelLlmApiKey");
  const prov = el("inlineLlmApiKeyProvider")?.value || "openai";
  if (label) label.textContent = LLM_PROVIDER_LABELS[prov] || "API key";
  const name = inlineSelectedLlmBackendKey();
  const info = keysData?.keys?.[name] ?? { configured: false, masked: "" };
  applySecretFieldHintAndPlaceholder("inline_key_llm_api_key", "inline_hint_llm_api_key", info);
}
async function loadInlineSettings() {
  try {
    const data = await fetchJSON("/api/settings/keys");
    lastApiKeysPayload = data;
    applyInlineLlmKeyHint(data);
  } catch (e) { console.error("loadInlineSettings:", e); }
  renderIntegrations();
}
async function saveInlineApiKeys() {
  const body = {};
  const llmInput = el("inline_key_llm_api_key");
  if (llmInput && llmInput.value.trim()) body[inlineSelectedLlmBackendKey()] = llmInput.value.trim();
  if (!Object.keys(body).length) { showToast("Please enter a value to save.", true); return; }
  try {
    showLoading("Saving...");
    await fetchJSON("/api/settings/keys", { method: "PUT", body: JSON.stringify(body) });
    if (llmInput) llmInput.value = "";
    showToast("Settings saved."); await loadInlineSettings();
  } catch (e) { showToast(String(e.message || e), true); }
  finally { hideLoading(); }
}

// ---------------------------------------------------------------------------
// Event bindings
// ---------------------------------------------------------------------------
// Inline settings event bindings
el("settingsInlineTabLlm")?.addEventListener("click", () => switchInlineSettingsTab("llm"));
el("settingsInlineTabIntegrations")?.addEventListener("click", () => switchInlineSettingsTab("integrations"));
el("btnInlineSaveApiKeys")?.addEventListener("click", () => saveInlineApiKeys().catch(e => showToast(String(e.message || e), true)));
el("inlineLlmApiKeyProvider")?.addEventListener("change", () => { const inp = el("inline_key_llm_api_key"); if (inp) inp.value = ""; applyInlineLlmKeyHint(lastApiKeysPayload || { keys: {} }); });
el("btnInlineClearLlmApiKey")?.addEventListener("click", () => clearApiKey(inlineSelectedLlmBackendKey()).catch(e => showToast(String(e.message || e), true)));
// Integrations cards (delegated)
el("integrationsContainer")?.addEventListener("click", (ev) => {
  const a = ev.target.closest("[data-action]");
  if (a) {
    const action = a.getAttribute("data-action");
    if (action === "figma-connect") figmaConnect().catch(e => showToast(String(e.message || e), true));
    else if (action === "figma-disconnect") figmaDisconnect().catch(e => showToast(String(e.message || e), true));
    else if (action === "figma-clear-cache") clearFigmaCache();
    else if (action === "atlassian-save") atlassianSave().catch(e => showToast(String(e.message || e), true));
    else if (action === "atlassian-disconnect") atlassianDisconnect().catch(e => showToast(String(e.message || e), true));
    else if (action === "atlassian-edit") { atlassianEditing = true; renderIntegrations(); }
    else if (action === "atlassian-cancel") { atlassianEditing = false; renderIntegrations(); }
    return;
  }
  const tbtn = ev.target.closest("[data-test-figma-input]");
  if (tbtn) testFigmaToken(tbtn.getAttribute("data-test-figma-input"));
});
el("btnRegister")?.addEventListener("click", tryRegister);
el("btnLogin")?.addEventListener("click", tryLogin);
el("btnLogout")?.addEventListener("click", logout);
el("btnCancelRequest")?.addEventListener("click", () => { genAbortController?.abort(); });

el("projectSwitcherBtn")?.addEventListener("click", toggleProjectSwitcherDropdown);
document.addEventListener("click", (e) => {
  if (!el("projectSwitcherWrap")?.contains(e.target)) el("projectSwitcherDropdown")?.classList.add("hidden");
});
el("btnSwitcherCreateProject")?.addEventListener("click", () => { el("projectSwitcherDropdown")?.classList.add("hidden"); openProjectModal(); });
el("btnHomeCreateProject")?.addEventListener("click", () => openProjectModal());

el("btnCloseProjectModal")?.addEventListener("click", () => el("projectModal")?.classList.add("hidden"));
el("btnProjectModalCancel")?.addEventListener("click", () => el("projectModal")?.classList.add("hidden"));
el("btnProjectModalSave")?.addEventListener("click", saveProjectModal);
el("projectModal")?.addEventListener("click", (e) => { if (e.target === el("projectModal")) el("projectModal")?.classList.add("hidden"); });

// Project actions kebab menu
(function wireProjectKebab() {
  const btn = el("projectKebabBtn");
  const menu = el("projectKebabMenu");
  if (!btn || !menu) return;
  const isOpen = () => !menu.classList.contains("hidden");
  const open = () => { menu.classList.remove("hidden"); btn.setAttribute("aria-expanded", "true"); };
  const close = () => { menu.classList.add("hidden"); btn.setAttribute("aria-expanded", "false"); };
  btn.addEventListener("click", (e) => { e.stopPropagation(); if (isOpen()) close(); else open(); });
  document.addEventListener("click", (e) => { if (isOpen() && !btn.parentElement.contains(e.target)) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && isOpen()) close(); });
  el("kebabEditProject")?.addEventListener("click", () => { close(); if (currentProjectId) openProjectModal(currentProjectId); });
  el("kebabDeleteProject")?.addEventListener("click", () => { close(); deleteProject(); });
})();

// Draft-from-file modal
el("btnOpenDraftFromFile")?.addEventListener("click", openDraftModal);
el("btnCloseDraftModal")?.addEventListener("click", closeDraftModal);
el("btnDraftCancel")?.addEventListener("click", closeDraftModal);
el("btnDraftUse")?.addEventListener("click", useDraftDescription);
el("draftFileInput")?.addEventListener("change", (ev) => {
  const f = ev.target.files?.[0];
  if (!f) return;
  el("draftFileName").textContent = f.name;
  draftGenerateFromFile(f).catch(e => showToast(String(e.message || e), true));
});
el("draftModal")?.addEventListener("click", (e) => { if (e.target === el("draftModal")) closeDraftModal(); });

el("btnBulkDelete")?.addEventListener("click", () => bulkDeleteSelected().catch(e => showToast(String(e.message || e), true)));
el("btnBulkClear")?.addEventListener("click", clearBulkSelection);

el("btnSidebarToggle")?.addEventListener("click", () => {
  if (el("sidebar")?.classList.contains("sidebar-open")) closeSidebarMobile();
  else openSidebarMobile();
});
el("sidebarBackdrop")?.addEventListener("click", closeSidebarMobile);

el("btnNewFeature")?.addEventListener("click", openFeatureModal);
el("btnCloseFeatureModal")?.addEventListener("click", () => el("featureModal")?.classList.add("hidden"));
el("btnFeatureModalCancel")?.addEventListener("click", () => el("featureModal")?.classList.add("hidden"));
el("btnFeatureModalSave")?.addEventListener("click", saveFeatureModal);
el("featureModal")?.addEventListener("click", (e) => { if (e.target === el("featureModal")) el("featureModal")?.classList.add("hidden"); });

el("btnCloseGenerateModal")?.addEventListener("click", () => el("generateModal")?.classList.add("hidden"));
el("generateModal")?.addEventListener("click", (e) => { if (e.target === el("generateModal")) el("generateModal")?.classList.add("hidden"); });
el("btnGenerateSubmit")?.addEventListener("click", submitGenerate);
el("genMultiInputMode")?.addEventListener("change", () => {
  const on = el("genMultiInputMode").checked;
  el("genSingleInputWrap")?.classList.toggle("hidden", on);
  el("genMultiInputWrap")?.classList.toggle("hidden", !on);
  if (on) { if (!multiBlocks.length) multiBlocks = [{ parserName: activeParser || parsers[0]?.name || "text" }]; renderGenMultiBlocks(); }
  else renderGenParsers();
  updateGenButton();
});
el("genBtnAddMultiBlock")?.addEventListener("click", () => { multiBlocks.push({ parserName: parsers[0]?.name || "text" }); renderGenMultiBlocks(); });

el("btnCloseIterateModal")?.addEventListener("click", () => el("iterateModal")?.classList.add("hidden"));
el("iterateModal")?.addEventListener("click", (e) => { if (e.target === el("iterateModal")) el("iterateModal")?.classList.add("hidden"); });
el("btnIterateSubmit")?.addEventListener("click", submitIterate);

el("btnOpenExport")?.addEventListener("click", openExportModal);
el("btnCloseExportModal")?.addEventListener("click", () => el("exportModal")?.classList.add("hidden"));
el("btnExportCancel")?.addEventListener("click", () => el("exportModal")?.classList.add("hidden"));
el("btnExportSubmit")?.addEventListener("click", submitExport);
el("exportModal")?.addEventListener("click", (e) => { if (e.target === el("exportModal")) el("exportModal")?.classList.add("hidden"); });

el("btnCloseTcDetail")?.addEventListener("click", () => el("tcDetailModal")?.classList.add("hidden"));
el("tcDetailModal")?.addEventListener("click", (e) => { if (e.target === el("tcDetailModal")) el("tcDetailModal")?.classList.add("hidden"); });
el("btnTcDetailSave")?.addEventListener("click", saveTcDetail);
el("btnTcDetailDelete")?.addEventListener("click", deleteTcFromDetail);
el("btnTcDetailAutoExec")?.addEventListener("click", () => openAutoExecModal());

el("btnCloseAutoExecModal")?.addEventListener("click", () => el("autoExecModal")?.classList.add("hidden"));
el("btnAutoExecCancel")?.addEventListener("click", () => el("autoExecModal")?.classList.add("hidden"));
el("autoExecModal")?.addEventListener("click", (e) => { if (e.target === el("autoExecModal")) el("autoExecModal")?.classList.add("hidden"); });
el("btnAutoExecRegenerate")?.addEventListener("click", () => regenerateAutoExecCode().catch(e => showToast(String(e.message || e), true)));
el("btnAutoExecSave")?.addEventListener("click", () => saveAutoExecCode().catch(e => showToast(String(e.message || e), true)));
el("btnAutoExecRun")?.addEventListener("click", () => runAutoExec());

el("btnCloseAdaptExpectedModal")?.addEventListener("click", closeAdaptExpectedModal);
el("btnAdaptExpectedCancel")?.addEventListener("click", closeAdaptExpectedModal);
el("adaptExpectedModal")?.addEventListener("click", (e) => { if (e.target === el("adaptExpectedModal")) closeAdaptExpectedModal(); });
el("btnRegenerateAdaptSuggestion")?.addEventListener("click", () => _fetchAdaptSuggestion().catch(e => showToast(String(e.message || e), true)));
el("btnAdaptExpectedSave")?.addEventListener("click", () => saveAndRerunAdaptedExpected().catch(e => showToast(String(e.message || e), true)));

document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") {
    el("generateModal")?.classList.add("hidden");
    el("iterateModal")?.classList.add("hidden");
    el("exportModal")?.classList.add("hidden");
    el("tcDetailModal")?.classList.add("hidden");
    el("projectModal")?.classList.add("hidden");
    el("featureModal")?.classList.add("hidden");
  }
});

// ---------------------------------------------------------------------------
// Dark mode toggle
// ---------------------------------------------------------------------------
const THEME_KEY = "tcg_theme";

function isDarkMode() {
  return document.documentElement.classList.contains("dark");
}

function applyTheme(dark) {
  document.documentElement.classList.toggle("dark", dark);
  const toggle = el("darkToggle");
  if (toggle) toggle.setAttribute("aria-checked", String(dark));
  try { localStorage.setItem(THEME_KEY, dark ? "dark" : "light"); } catch (_) {}
}

function initTheme() {
  let dark = false;
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === "dark") dark = true;
    else if (stored === "light") dark = false;
    else dark = window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
  } catch (_) {}
  applyTheme(dark);
}

function toggleTheme() {
  applyTheme(!isDarkMode());
  if (currentSubView === "dashboard") {
    _destroyDashCharts();
    loadDashboard();
  }
}

initTheme();

el("darkToggle")?.addEventListener("click", toggleTheme);
el("darkToggle")?.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleTheme(); }
});

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
(async function init() {
  initOverviewSections();
  genPrefTypesWidget = mountTestTypeMultiSelect({ containerId: "genPreferredTypes" });
  iterPrefTypesWidget = mountTestTypeMultiSelect({ containerId: "iterPreferredTypes" });
  await loadParsers();
  try { await refreshProjects(); await refreshUserChip(); }
  catch (e) { if ((e.message || "").includes("401")) el("authSection")?.classList.remove("hidden"); }
  await loadInlineSettings();
  try {
    if (!window.location.hash || window.location.hash === "#") {
      const s = localStorage.getItem(HASH_STORAGE_KEY);
      const hash = s && s !== "#" ? (s.startsWith("#") ? s : `#${s}`) : "#/";
      history.replaceState(null, "", new URL(window.location.href).origin + window.location.pathname + hash);
    }
  } catch (_) { history.replaceState(null, "", new URL(window.location.href).origin + window.location.pathname + "#/"); }
  await handleRoute().catch(e => console.error(e));
  window.addEventListener("hashchange", () => handleRoute().catch(e => console.error(e)));

  // Image preview modal (used by Generations tab screenshots)
  const imgModal = el("imageModal");
  const imgClose = el("imageModalClose");
  if (imgClose) imgClose.addEventListener("click", closeImageModal);
  if (imgModal) {
    imgModal.addEventListener("click", (e) => {
      if (e.target === imgModal) closeImageModal();
    });
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeImageModal(); closeDraftModal(); }
  });
})();
