# Browser session recording

The **Browser Session** input type lets you record a real browser interaction and generate test cases from the recorded flow. It works with either the **Playwright MCP** or the **Cursor IDE Browser MCP**.

## Two-phase flow

1. **Record** — Create a session, execute steps, capture results.
2. **Generate** — Feed the completed session into `POST /api/generate` with `input_type: "browser_session"`.

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/browser-session/start` | Create a session `{ project_id, url, feature_name, browser_type, steps: string[] }`. Each string in `steps[]` becomes the `instruction` of a pending step. |
| GET | `/api/browser-session/{id}` | Retrieve session with all recorded steps |
| GET | `/api/browser-session/project/{pid}` | List sessions for a project |
| POST | `/api/browser-session/{id}/step` | Add a recorded step (full object) `{ instruction, action_type, target, value, snapshot_yaml, screenshot_b64, vision_description, status }` |
| PUT | `/api/browser-session/{id}/step/{index}` | Update a step by index |
| POST | `/api/browser-session/{id}/complete` | Mark session as `completed` or `failed` |

## Agent-mediated workflow (recommended)

The Cursor agent orchestrates the recording by calling MCP tools:

1. User opens the Generate modal, selects **Browser Session** tab, enters URL and steps.
2. Frontend calls `POST /api/browser-session/start` to create the session with pending steps.
3. The Cursor agent reads the session, then for each step:
   - Calls `browser_navigate` (Playwright or IDE Browser MCP) to open the URL
   - Calls `browser_snapshot` to capture the accessibility tree
   - Interprets the step instruction and calls the appropriate MCP tool (`browser_click`, `browser_type`, etc.)
   - Calls `browser_take_screenshot` to capture the result
   - Optionally describes the screenshot via a vision model
   - Posts the step result back via `POST /api/browser-session/{id}/step`
4. Agent calls `POST /api/browser-session/{id}/complete` when done.
5. User clicks **Generate test cases** — the `browser_session` parser reads the recorded session and the LLM generates test cases from the full context (steps, snapshots, screenshots).

## Frontend UX

The Browser Session tab in the generate modal provides:

- URL input and step instructions (one per line)
- Browser type selector (Playwright / IDE Browser)
- "Start Recording" button to create the session
- Live step progress with status indicators
- Interactive "Add Step" input during recording
- "Complete Recording" button to finalize
