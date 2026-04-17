# ASLAN — IRIS ID web chatbot backend

Runtime RAG stack aligned with `chatbot-rag.pdf`: **documents live in Qdrant**, not in Cursor context. This repo holds **ingestion + retrieval code + API** only.

## Layout

- `app/` — FastAPI (`/chat`, `/health`), routing orchestration, FAQ layer, regional contacts
- `rag/` — `ingest.py`, `query.py`, embeddings, filters, rerank stub
- `faq/` — small static FAQ JSON per language (`faq_en.json`, …)
- `config/` — settings; copy `contacts.example.json` → `contacts.json` for real contacts

## Quick start

1. `python -m venv .venv && source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. Copy `.env.example` → `.env` and set `OPENAI_API_KEY` and/or `GOOGLE_API_KEY`, `LLM_PROVIDER`, Qdrant URL
4. `docker compose up -d` (Qdrant)
5. Ingest sample docs from a **folder outside this repo**:

   `python -m rag.ingest /path/to/corpus --prefix manuals --product iA1000 --language en --doc-type manual --department support --version 2026-04 --access internal`

6. Run API: `python -m app.main` or `uvicorn app.main:app --host 0.0.0.0 --port 8000`

## Cursor / tokens

Keep private corpora out of the workspace; use `.cursorignore` (see PDF). Agents should edit **code**, not bulk-read PDFs.

## Embedding dimensions

Use **one** embedding stack consistently: OpenAI embeddings (e.g. 1536-dim) **or** Gemini `text-embedding-004` (768-dim). The ingest script probes dimension on first embedding and creates the Qdrant collection accordingly.
