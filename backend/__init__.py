"""AutoQA Studio backend package.

Layout:
- ``main.py`` — FastAPI app and static frontend mount
- ``routers/`` — HTTP API surface (thin: validate → repo/service → response)
- ``repositories/`` — SQLite access
- ``services/`` — LLM, parsers, export, dedup, auth helpers
- ``models/`` — Pydantic domain + request DTOs
- ``prompts/`` — LLM user message builders

Run the app: ``python -m uvicorn backend.main:app`` from the project root
(so ``backend`` is importable as a package).
"""
