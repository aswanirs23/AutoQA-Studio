# AutoQA Studio

AI-powered QA studio that **generates and executes** test cases. Turn manual text, Figma designs, Jira issues, screenshots, and recorded browser sessions into structured, traceable test cases — then auto-run them as sandboxed Playwright tests with pass/fail results and screenshots. A single-process FastAPI app with a SQLite store and a vanilla-JS SPA; works with OpenAI, Anthropic, or Google Gemini.

## Features

- **Multi-source generation** — manual text, Figma, Jira, screenshots (vision), and browser recordings, combinable in one request via a plugin parser architecture.
- **Auto-execute** — turn any test case into runnable Playwright code, run it against your base URL, and get pass/fail/error with a screenshot and console output. Generated code is cached, editable, and re-runnable.
- **Projects → features → test cases** — persisted in SQLite with full CRUD, project-wide dedup, source traceability, stats, and generation history.
- **Multi-LLM** — OpenAI, Anthropic, and Gemini with automatic provider fallback. API keys are stored in the database, never in `.env`.
- **Export** — Excel, CSV, JSON, or TestRail-style CSV, with feature / priority / search filters.
- **UI** — project gallery, sidebar workspace (Overview, Test Cases, Dashboard, Settings), Chart.js dashboard, dark mode, responsive.

## Requirements

- Python 3.11+
- At least one LLM API key: OpenAI, Anthropic, and/or Google Gemini

## Quick start (Linux/macOS)

[start.sh](start.sh) installs dependencies, builds the CSS, and starts the server — you only create the venv and add a key once:

```bash
git clone https://github.com/aswanirs23/AutoQA-Studio.git
cd AutoQA-Studio

python3 -m venv .venv        # one-time
cp .env.example .env         # then add at least one LLM API key

./start.sh                   # runs on http://localhost:8080
```

Open **http://localhost:8080/**. On re-runs just `./start.sh` again.

On Windows or if you prefer explicit steps, see [manual setup](docs/configuration.md#manual-setup).

## Configuration

- **Non-secret config** (provider defaults, DB path, auth) goes in `.env` — copy it from [.env.example](.env.example).
- **API keys** (LLM providers, Figma, Jira) are entered in the **Settings UI** and stored in SQLite; `.env` values for these are intentionally ignored.

Full environment-variable reference: [docs/configuration.md](docs/configuration.md).

## Documentation

- [Configuration](docs/configuration.md) — environment variables, manual / Windows setup
- [Deployment](docs/deployment.md) — production config, Tailwind build, Docker, hosting notes
- [API reference](docs/api.md) — REST endpoints and request shapes
- [Input parsers](docs/parsers.md) — built-in parsers and how to add one
- [Browser session recording](docs/browser-sessions.md) — record a flow, then generate tests
- [Auto-execute](docs/auto-execute.md) — run test cases as Playwright; security boundaries
- [Architecture](docs/architecture.md) — project layout
- [Known limitations & roadmap](docs/limitations.md) — what it doesn't do yet, and proposed fixes

## License

Released under the [MIT License](LICENSE) © Aswani.
