# API reference

Interactive OpenAPI docs are served at `/docs` when the app is running.

Send `Authorization: Bearer <token>` when `AUTH_DISABLED=false`.

## Overview

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/register` | Register (when `AUTH_DISABLED=false`) |
| POST | `/api/auth/login` | Login → JWT |
| GET | `/api/auth/me` | Current user |
| POST | `/api/projects` | Create project `{ "name", "description" }` |
| GET | `/api/projects` | List projects |
| GET | `/api/projects/{id}` | Project + features |
| PUT | `/api/projects/{id}` | Update project `{ "name", "description", "base_url" }` |
| DELETE | `/api/projects/{id}` | Delete (cascade) |
| POST | `/api/projects/{pid}/generate-description` | Upload file → AI-generated project description |
| POST | `/api/projects/{pid}/features` | Create feature |
| GET | `/api/projects/{pid}/features` | List features |
| PUT | `/api/projects/{pid}/features/{fid}` | Update feature |
| DELETE | `/api/projects/{pid}/features/{fid}` | Delete feature (cascade) |
| GET | `/api/projects/{pid}/test-cases?feature_id=` | List test cases |
| PATCH | `/api/projects/{pid}/test-cases/{tc_id}` | Update test case fields |
| DELETE | `/api/projects/{pid}/test-cases/{tc_id}` | Delete one test case |
| POST | `/api/projects/{pid}/test-cases/bulk-delete` | Body `{ "ids": ["TC_001", ...] }` |
| GET | `/api/projects/{pid}/stats` | Counts by type, priority, feature |
| GET | `/api/projects/{pid}/input-history?limit=` | Recent generation runs |
| GET | `/api/parsers` | Parser metadata (dynamic UI) |
| POST | `/api/generate` | JSON or multipart — optional `min_test_cases`, `preferred_test_types` |
| POST | `/api/generate/iterate` | Instruction + optional `feature_id`, `type_filter`, `min_test_cases`, `preferred_test_types` |
| GET | `/api/export/{project_id}?format=&feature_ids=&search=&priority=` | Export with filters |
| GET | `/api/settings/keys` | API key status (configured, masked) |
| PUT | `/api/settings/keys` | Save or clear API keys |
| POST | `/api/browser-session/start` | Create browser session |
| GET | `/api/browser-session/{id}` | Get session details |
| POST | `/api/browser-session/{id}/step` | Add recorded step |
| POST | `/api/browser-session/{id}/complete` | Complete/fail session |

Auto-execute endpoints are documented in [auto-execute.md](auto-execute.md); the full browser-session API is in [browser-sessions.md](browser-sessions.md).

## `POST /api/generate`

**JSON** (no file): set `input_type` to one of the built-in parsers (see [parsers.md](parsers.md)).

```json
{
  "input_type": "text",
  "project_id": "<uuid>",
  "feature_id": "<uuid>",
  "data": { "feature_name": "login", "content": "..." },
  "llm_provider": "openai"
}
```

**Multipart** (image upload / **screenshot** parser): `input_type=screenshot`, `project_id`, `feature_id`, `data` (JSON string), `file` (image), optional `llm_provider`, `llm_model`, `min_test_cases`, `preferred_test_types`.

**Multiple sources in one request**: send `inputs` instead of `input_type` / `data`. Each item has `input_type`, `data`, and optionally `file_index` when using multipart files.

```json
{
  "project_id": "<uuid>",
  "feature_id": "<uuid>",
  "inputs": [
    { "input_type": "text", "data": { "feature_name": "checkout", "content": "User must confirm email." } },
    { "input_type": "jira", "data": { "issue_key": "PROJ-42" } }
  ]
}
```

## `POST /api/generate/iterate`

```json
{
  "project_id": "<uuid>",
  "instruction": "Add more edge cases for validation",
  "feature_id": "<uuid>",
  "type_filter": "edge",
  "min_test_cases": 5,
  "preferred_test_types": ["edge", "negative"],
  "llm_provider": "anthropic",
  "llm_model": "claude-sonnet-4-20250514"
}
```
