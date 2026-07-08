# Auto-execute generation improvements (v2) — design

**Date:** 2026-07-08
**Status:** approved, ready for implementation planning
**Builds on:** [2026-07-07-authenticated-auto-execute-design.md](2026-07-07-authenticated-auto-execute-design.md)

## Problem

With login/session working, three friction points remain when auto-executing on a real app:

1. **Tests navigate to `/`.** The generator defaults to `goto(base_url + '/')`. For many apps `/` is the login/marketing page and the real content is elsewhere (SauceDemo: `/inventory.html`). Authenticated tests land on the login screen and fail.
2. **Login-flow tests use invented credentials.** A "valid login" test generates `fill('valid_username')` / a guessed password instead of the credentials the user configured — and, because the saved session auto-logs-in, the login form isn't even shown.
3. **Confusing verify error.** When *Test login & save session* succeeds at login but the success-check doesn't match, the error ("did not reach the expected state") misleads.

## Goal

Generated tests reach the right page and, for login-flow tests, exercise the real login with the configured credentials — while credentials still never live in stored test code.

## Scope

**In scope (v2):**
- Non-login tests start on the app's authenticated **landing path** (not `/`).
- **Login-flow tests** run logged-out and use the configured username/password, injected at run time.
- Auto-detect login-intent from the test title/steps, with a manual run-time override toggle.
- Clearer *verify* error message.

**Out of scope (deferred):** DOM-snapshot-aware selector generation (bigger, separate increment); multi-step/MFA login.

## Decisions (from brainstorming)

- **Landing path source:** auto-derive from `auth_config.success_check` when it is a path (starts with `/`); otherwise use a new optional `auth_config.home_path`. If neither is set, fall back to `/` (current behavior).
- **Login detection:** auto-detect from title/steps, plus a manual "Login test (run logged out)" toggle in the Auto-execute modal to override.
- **DOM-aware:** deferred.

## Behavior

### Landing path (Part A)
`resolve_landing_path(auth_config) -> str`:
- `home_path` if set (non-empty), else
- `success_check` if it starts with `/`, else
- `""` (caller treats empty as `/`).

Non-login test generation navigates to `base_url + landing_path` (defaulting to `/` when empty). No change for apps where `/` is correct.

### Login detection (Part B)
`is_login_test(title, steps) -> bool`: true when the title or any step (lowercased) matches a login cue — one of `"log in"`, `"login"`, `"sign in"`, `"sign-in"`, `"signin"`, `"log-in"`. This is the default; a client flag overrides it.

### Credential injection (Part B)
- Generated test signature MAY be `async def test(page, base_url, username, password)`. The runner wrapper **inspects the function's parameter count** and calls it with 2 or 4 args accordingly — existing `test(page, base_url)` code keeps working unchanged.
- The runner substitutes the project's stored username/password into the wrapper as `USERNAME`/`PASSWORD` constants (server-side template substitution, ephemeral tmpfile — same mechanism as the login wrapper). Generated code references the `username`/`password` **parameters**, never literals, so **stored code contains no credentials**.
- The generator, for a login-flow test, produces code that navigates to the login page and fills `username` / `password`; the system prompt is updated to use these variables instead of inventing `valid_username`.

### Execution auth mode (Parts A+B)
In `run_playwright`:
- `logged_out = body.logged_out or is_login_test(tc.title, tc.steps)`.
- If `logged_out`: pass `storage_state_path=None` (fresh, logged-out context) so the login form is present; still inject credentials.
- Else: pass the saved session path (current behavior); credentials are injected too but 2-arg code ignores them.

### Generation mode (Parts A+B)
`generate-playwright` computes `is_login = body.login_mode if provided else is_login_test(tc.title, tc.steps)` and `landing_path = resolve_landing_path(auth_config)`, and passes both into `generate_playwright_code` → `build_playwright_user_message`. Login-flow prompt vs. authenticated-page prompt differ only in the navigation target and whether they use `username`/`password`.

### Clearer verify error (Part C)
The login wrapper, on a failed success-check, reports the URL it actually reached and the check that failed, e.g.:
`Logged in and reached '/inventory.html', but success check 'Products' was not found on the page.`
(vs. the generic "did not reach the expected state".) `capture_login_session` surfaces this message.

## Data model
No schema change. `auth_config` gains an optional `home_path` key (JSON, already flexible). `SaveAuthBody` gains `home_path: str = ""`.

## API
- `SaveAuthBody` + `home_path`.
- `GenerateBody` gains `login_mode: bool | None = None` (None = auto-detect).
- `RunBody` gains `logged_out: bool = False`.
- Masking, endpoints otherwise unchanged.

## Frontend
- **Login setup:** add an optional **"App home path"** field (e.g. `/inventory.html`), saved into `auth_config.home_path`.
- **Auto-execute modal:** add a **"Login test (run logged out)"** checkbox. When checked, Regenerate sends `login_mode=true` and Run sends `logged_out=true`; unchecked → both omit the flag (server auto-detects).

## Testing
- **Unit:** `resolve_landing_path` (home_path > success_check-path > empty); `is_login_test` (positive/negative cues); wrapper arg-count dispatch (2-arg vs 4-arg function both invoked correctly — can be tested by running a tiny 4-arg `test` through `run_playwright_code` with injected creds and asserting they were used).
- **Integration (local server):** extend the existing hermetic login-form fixture — a login test run **logged-out with injected creds** logs in and passes; a non-login test with `home_path` set navigates there. Reuse `tests/services/test_auth_session_integration.py` patterns and the `_data_dir` monkeypatch.
- **Router:** `run-playwright` forwards `storage_state_path=None` for a login-detected test and forwards creds; `generate-playwright` respects `login_mode`. Use the isolated `tests/routers/conftest.py`.
- Full suite green before each commit.

## Security summary
Unchanged from v1: credentials in SQLite only, masked in API, never in git/env/LLM-prompt/**stored code**. Injection is at run time into the ephemeral wrapper only. Denylist still applies to the user test code; login wrapper remains the only denylist-exempt trusted script.
