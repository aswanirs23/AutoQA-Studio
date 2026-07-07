# Configuration

## Environment variables

**Non-secret configuration** (set in `.env`):

| Variable | Purpose |
|----------|---------|
| `LLM_PROVIDER` | Default provider: `openai`, `anthropic`, or `gemini` |
| `OPENAI_MODEL` | Default `gpt-4o` |
| `ANTHROPIC_MODEL` | Default `claude-sonnet-4-20250514` |
| `GEMINI_MODEL` | Default `gemini-2.0-flash` |
| `DATABASE_PATH` | SQLite file (default `data/testgen.db`) |
| `AUTH_DISABLED` | `true` = no login required (default). `false` = JWT auth enabled |
| `JWT_SECRET` | Required when `AUTH_DISABLED=false` |

**API keys / secrets** (configured via the Settings UI, stored in SQLite — `.env` values for these are ignored):

| Key | Purpose |
|-----|---------|
| OpenAI API key | GPT-4o (text + vision) |
| Anthropic API key | Claude (text + vision) |
| Google Gemini API key | Gemini (text + vision) |
| Figma access token | Figma REST API |
| Jira base URL | e.g. `https://your-domain.atlassian.net` |
| Jira email | Jira account email |
| Jira API token | Atlassian API token |

If the default provider's key is not configured, the app **automatically falls back** to another configured provider.

## Manual setup

Prefer explicit steps, or on Windows where `start.sh` doesn't run? Do it by hand:

```bash
git clone https://github.com/aswanirs23/AutoQA-Studio.git
cd AutoQA-Studio

python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

pip install -r requirements.txt
copy .env.example .env          # then edit .env

# Optional — build the Tailwind CSS. Skip for local dev (Tailwind loads via
# CDN in the browser); required for production or after adding new CSS classes.
npm install
npm run build:css
```

Then run:

```bash
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8080
```

Open **http://127.0.0.1:8080/** for the web UI, or **http://127.0.0.1:8080/docs** for OpenAPI.

The UI loads parser tabs (Manual text, Figma, Jira, Screenshot, Browser Session) from `GET /api/parsers`. If you run the frontend from a separate dev server (e.g. Vite on 5173, Live Server on 5500), the script auto-detects the API at `http://127.0.0.1:8080`, or set `localStorage.tcg_api_base` manually.
