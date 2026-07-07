# Deployment

Local dev uses the [Quick start](../README.md#quick-start-linuxmacos) (`./start.sh`) or the [manual setup](configuration.md#manual-setup). For a deployable build, use the steps here.

## Production environment variables

Set these in your deployment environment (or in `.env` on the host):

| Variable | Production value | Notes |
|----------|------------------|-------|
| `LOG_LEVEL` | `INFO` | `DEBUG` while bringing up a new env, then drop to `INFO` |
| `CORS_ORIGINS` | `https://your-domain.example` | Comma-separated allowlist. Don't ship `*`. Same-origin (FastAPI serves the UI itself) means CORS isn't strictly required, but lock it anyway |
| `DATABASE_PATH` | `/app/data/testgen.db` | Mount a persistent volume at `/app/data` |
| `AUTH_DISABLED` | `true` for now | Flip to `false` once a login UI is added; until then keep behind a private network |
| `JWT_SECRET` | (any long random) | Required when `AUTH_DISABLED=false` |
| `LLM_PROVIDER` | `openai` / `anthropic` / `gemini` | Default provider. Per-provider API keys go in the **Settings UI** (SQLite), not env vars |

## Build the production Tailwind CSS

The dev build loads Tailwind from a browser CDN (JIT in the browser). For production, build a minified CSS file once.

**With Node** (recommended):

```bash
npm install
npm run build:css
```

This produces `frontend/styles-tailwind.css` (minified). Repeat after any change to `index.html` or `app.js` that introduces new Tailwind utility classes. During UI work, `npm run watch:css` rebuilds on save.

**Without Node** — use the standalone Tailwind v4 CLI binary from [Tailwind's GitHub releases](https://github.com/tailwindlabs/tailwindcss/releases) (single executable, no Node required):

```bash
./tailwindcss -i ./frontend/tailwind.css -o ./frontend/styles-tailwind.css --minify
```

## Docker (one-shot build & run)

The provided `Dockerfile` is multi-stage: a Node stage builds the Tailwind CSS, then a slim Python stage runs uvicorn. No Node toolchain is required on the host.

```bash
docker build -t testcase-agent .
docker run --rm -p 8080:8080 \
  --env-file .env \
  -v $(pwd)/data:/app/data \
  testcase-agent
```

On Windows PowerShell:

```powershell
docker run --rm -p 8080:8080 `
  --env-file .env `
  -v ${PWD}/data:/app/data `
  testcase-agent
```

The mounted `data/` volume persists the SQLite DB across container restarts. Health: `GET http://localhost:8080/health` returns `{"status":"ok"}`.

## Hosting notes

- **Bind to `0.0.0.0`** in any non-Docker run on a server: `python -m uvicorn backend.main:app --host 0.0.0.0 --port 8080`. The dev `127.0.0.1` only accepts localhost connections.
- **Single uvicorn worker.** SQLite + multiple workers can corrupt writes. If you outgrow this, migrate to Postgres before scaling workers.
- **Put a reverse proxy (nginx / Caddy / Traefik) in front** for TLS, gzip, and request size limits. Point it at `http://app:8080`.
- **Persist `/app/data`** — the SQLite DB and any uploaded artifacts live there. Lose this volume, lose all projects and test cases.
- **Health check endpoint**: `GET /health` for platform liveness probes (Render, Fly, Railway, K8s).
