# Login Page Redesign — Design Spec

**Date:** 2026-07-15
**Status:** Approved (design), pending implementation plan
**Scope:** Frontend only. No backend / API changes.

## Problem

With `AUTH_DISABLED=false`, the sign-in UI is a small overlay card (`#authSection`)
rendered *on top of* the fully-loaded app shell. Two issues:

1. It looks unfinished — a bare amber-bordered modal, no branding, dev-only helper text.
2. The app chrome (header, sidebar, page content) renders behind the overlay, so login
   reads as a modal rather than a gate.

Goal: replace it with a proper full-page login that carries the product's name, presents
email → password → login top-to-bottom, offers registration, and surfaces errors inline.

## Decisions

| Decision | Choice |
| --- | --- |
| Forgot password | **Placeholder link only.** Appears after a failed login; inert for now. The backend has no reset flow (no email infra, no reset tokens), so a real reset is explicitly out of scope. |
| Visual direction | **Light & clean (BrowserStack-like).** White centered card on a light-grey field. Deliberately distinct from the app's dark theme; the login page stays light regardless of the app theme toggle. |
| Register UX | **Sign in ⇄ Create account toggle** in-place. Register view adds a Name field. |

## Layout

Full-page, opaque, covering the app until authenticated:

- **Top bar:** brand mark (`◆`, `#1856FF`) + "AutoQA Studio".
- **Body:** light-grey field, white card (~400px, centered).
- **Card (Sign in):** heading "Sign in" → Email → Password → inline error + "Forgot password?"
  (both hidden until a failed attempt) → full-width primary **Login** button →
  "Not registered yet? **Sign up**" link.
- **Card (Create account):** heading "Create account" → Name → Email → Password →
  full-width **Create account** button → "Already have an account? **Sign in**" link.

## Behavior

- **Gate:** the page is fully opaque and covers the app shell until a valid token exists.
  This resolves the "app visible behind the modal" issue by construction.
- **Mode toggle:** `setAuthMode('login' | 'register')` swaps headings, visible fields,
  primary button label, and the footer link. No reload. Switching modes clears any error.
- **Failed login/register (400/401):** render the server-appropriate message *inside the
  card* (e.g. "Incorrect email or password") instead of the current toast, and reveal the
  inert **Forgot password?** link. Error clears when the user edits a field or switches mode.
- **Success:** store token, hide the login page, load the app — unchanged from today
  (`setToken`, `refreshProjects`, `refreshUserChip`, `handleRoute`).
- **Register** now sends the **Name** field value (currently hardcoded to `"User"`).
- **Logout / 401:** re-show the full-page login (existing triggers keep working).

## Files touched

- `frontend/index.html` — rewrite the `#authSection` markup: top bar, card, both mode
  views, and new elements (`authName`, inline error node, forgot-password link, mode-toggle
  links). Remove the dev-only "Set AUTH_DISABLED=true" helper text.
- `frontend/app.js` — `tryLogin` / `tryRegister` inline-error handling (replace toast on
  auth failure), add `setAuthMode()`, send `name` from `authName` on register, wire the
  toggle and field-edit listeners that clear errors.

## Styling approach

Use inline styles / already-built classes with **explicit light colors** for the login
page, rather than the app's `--bg-*` theme tokens (which are dark). This keeps the login
light in both app themes and avoids introducing new Tailwind utility classes that would
require re-running `npm run build:css`.

## Out of scope

- Real password reset (email/token flow) — no backend email infrastructure exists.
- Any backend, router, or `/api/auth/*` change.
- Changes to the logged-in app, header user chip, or logout button behavior.

## Success criteria

1. With `AUTH_DISABLED=false`, visiting the app shows a full-page, branded, light login
   with no app chrome visible behind it.
2. Email → Password → Login stack, with a working Sign up ⇄ Sign in toggle (Name field in
   register).
3. A wrong password shows an inline error inside the card and reveals the Forgot password?
   link; a correct login loads the app.
4. Registering a new user persists the entered Name (visible in the header user chip).
5. No backend files changed; `styles-tailwind.css` does not need a rebuild.
