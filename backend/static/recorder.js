/**
 * TCG Browser Session Recorder
 *
 * Injected into a page via Playwright MCP's browser_evaluate.
 * Captures user interactions (click, type, select, submit, navigation)
 * and stores them in window.__tcg_recorder.events.
 *
 * Usage (from the agent):
 *   Inject:  browser_evaluate({ function: "<this entire script>" })
 *   Harvest: browser_evaluate({ function: "() => JSON.stringify(window.__tcg_recorder.flush())" })
 */
(() => {
  if (window.__tcg_recorder) return;

  const events = [];
  let inputTimer = null;
  let lastInputTarget = null;
  let lastInputValue = '';
  let lastUrl = location.href;

  // ── Selector builder ─────────────────────────────────────────────
  function buildSelector(el) {
    if (!el || el === document || el === document.body) return 'body';
    if (el.id) return '#' + CSS.escape(el.id);
    const testId = el.getAttribute('data-testid') || el.getAttribute('data-test-id');
    if (testId) return '[data-testid="' + testId + '"]';
    if (el.name && el.tagName) {
      return el.tagName.toLowerCase() + '[name="' + el.name + '"]';
    }
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel) return el.tagName.toLowerCase() + '[aria-label="' + ariaLabel + '"]';

    // Fallback: build a short path
    const tag = el.tagName.toLowerCase();
    const parent = el.parentElement;
    if (!parent) return tag;
    const siblings = Array.from(parent.children).filter(c => c.tagName === el.tagName);
    if (siblings.length === 1) return buildSelector(parent) + ' > ' + tag;
    const idx = siblings.indexOf(el) + 1;
    return buildSelector(parent) + ' > ' + tag + ':nth-of-type(' + idx + ')';
  }

  // ── Human-readable label for an element ──────────────────────────
  function labelFor(el) {
    if (!el) return '(unknown)';
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel) return ariaLabel.slice(0, 60);
    const placeholder = el.getAttribute('placeholder');
    if (placeholder) return placeholder.slice(0, 60);
    const text = (el.textContent || '').trim();
    if (text && text.length <= 60) return text;
    if (text) return text.slice(0, 57) + '...';
    const name = el.getAttribute('name');
    if (name) return name;
    return buildSelector(el);
  }

  // ── Human-readable description ───────────────────────────────────
  function describe(action, el, value) {
    const label = labelFor(el);
    const tag = el ? el.tagName.toLowerCase() : '?';
    const type = el ? (el.getAttribute('type') || '') : '';

    switch (action) {
      case 'click': {
        if (tag === 'a') return "Click link '" + label + "'";
        if (tag === 'button' || type === 'submit') return "Click button '" + label + "'";
        if (tag === 'input' && (type === 'checkbox' || type === 'radio'))
          return (el.checked ? 'Check' : 'Uncheck') + " '" + label + "'";
        return "Click on '" + label + "'";
      }
      case 'type':
        return "Type '" + (value || '').slice(0, 80) + "' into '" + label + "'";
      case 'select':
        return "Select '" + (value || '') + "' in '" + label + "'";
      case 'submit':
        return "Submit form" + (label !== '(unknown)' ? " '" + label + "'" : '');
      case 'keypress':
        return "Press " + (value || 'key') + " on '" + label + "'";
      case 'navigate':
        return "Navigate to " + (value || location.href);
      default:
        return action + " on '" + label + "'";
    }
  }

  function pushEvent(action, el, value) {
    events.push({
      ts: Date.now(),
      action: action,
      selector: el ? buildSelector(el) : '',
      description: describe(action, el, value),
      tag: el ? el.tagName.toLowerCase() : '',
      value: value || '',
      url: location.href,
    });
  }

  // ── Flush pending input before another event type ────────────────
  function flushPendingInput() {
    if (inputTimer) {
      clearTimeout(inputTimer);
      inputTimer = null;
    }
    if (lastInputTarget) {
      pushEvent('type', lastInputTarget, lastInputValue);
      lastInputTarget = null;
      lastInputValue = '';
    }
  }

  // ── Click ────────────────────────────────────────────────────────
  document.addEventListener('click', function (e) {
    flushPendingInput();
    const t = e.target;
    if (!t || t === document || t === document.documentElement) return;
    // Skip clicks on inputs/textareas -- those will be captured as type events
    const tag = t.tagName.toLowerCase();
    if ((tag === 'input' || tag === 'textarea') &&
        !['submit', 'button', 'checkbox', 'radio', 'file'].includes(t.type))
      return;
    pushEvent('click', t);
  }, true);

  // ── Input (debounced: collapse rapid keystrokes into one event) ──
  document.addEventListener('input', function (e) {
    const t = e.target;
    if (!t) return;
    const tag = t.tagName.toLowerCase();
    if (tag !== 'input' && tag !== 'textarea' && !t.isContentEditable) return;

    if (lastInputTarget === t) {
      lastInputValue = t.value || t.textContent || '';
    } else {
      flushPendingInput();
      lastInputTarget = t;
      lastInputValue = t.value || t.textContent || '';
    }
    if (inputTimer) clearTimeout(inputTimer);
    inputTimer = setTimeout(flushPendingInput, 800);
  }, true);

  // ── Change (selects, checkboxes after click is captured) ─────────
  document.addEventListener('change', function (e) {
    const t = e.target;
    if (!t) return;
    const tag = t.tagName.toLowerCase();
    if (tag === 'select') {
      flushPendingInput();
      const text = t.options[t.selectedIndex]
        ? t.options[t.selectedIndex].text
        : t.value;
      pushEvent('select', t, text);
    }
  }, true);

  // ── Submit ───────────────────────────────────────────────────────
  document.addEventListener('submit', function (e) {
    flushPendingInput();
    pushEvent('submit', e.target);
  }, true);

  // ── Special keydown (Enter, Escape, Tab) ─────────────────────────
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' || e.key === 'Escape' || e.key === 'Tab') {
      flushPendingInput();
      pushEvent('keypress', e.target, e.key);
    }
  }, true);

  // ── URL change detection (SPA navigation) ────────────────────────
  const origPushState = history.pushState;
  history.pushState = function () {
    origPushState.apply(this, arguments);
    checkUrlChange();
  };
  const origReplaceState = history.replaceState;
  history.replaceState = function () {
    origReplaceState.apply(this, arguments);
    checkUrlChange();
  };
  window.addEventListener('popstate', checkUrlChange);
  window.addEventListener('hashchange', checkUrlChange);

  function checkUrlChange() {
    const current = location.href;
    if (current !== lastUrl) {
      flushPendingInput();
      pushEvent('navigate', null, current);
      lastUrl = current;
    }
  }

  // ── Public API ───────────────────────────────────────────────────
  window.__tcg_recorder = {
    events: events,
    flush: function () {
      flushPendingInput();
      const copy = events.slice();
      events.length = 0;
      return copy;
    },
    count: function () {
      return events.length + (lastInputTarget ? 1 : 0);
    },
  };
})();
