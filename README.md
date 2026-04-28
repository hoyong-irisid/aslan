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

   **Partner-only manuals** (not committed): put files next to the repo under `../aslan-rag/partner_docs`, or set `PARTNER_DOCS_DIR` in `.env`. Then:

   `python -m rag.ingest --partner --product iA1000 --language en`

   To show partner-only manual figures in chat, add `figures.json` under the same partner docs directory:

   `../aslan-rag/partner_docs/figures.json`

   Example:
   `{"figures":[{"id":"ia1000-rear-installation-plate","title":"iA1000 Rear View with Installation Plate","file":"assets/iA1000_rear_view_installation_plate.png","aliases":["rear view installation plate","section 4.3 ia1000 rear view"]}]}`

   You can auto-generate `assets/` + `figures.json` from a partner PDF:

   `python -m rag.extract_partner_figures --pdf "/absolute/path/to/iA1000_User_Manual.pdf"`

   Noise filtering (small icons/logos/duplicates) is enabled by default. You can tune:

   `python -m rag.extract_partner_figures --min-side-px 120 --min-area-px 40000 --clean-generated-assets`

6. Run API: `python -m app.main` or `uvicorn app.main:app --host 0.0.0.0 --port 8000`

## Partner chat + email transcript

- Widget header **Partner** prompts for the partner code; **Verified** shows after successful `/chat` auth.
- Closing the panel clears the partner session (same as before).
- Optional: set **Resend** (`RESEND_API_KEY`, `RESEND_FROM`) or **SMTP** (`SMTP_*`) in `.env` for **Email chat transcript** (`POST /email/chat-transcript`, partner token required). Resend works from **localhost** (outbound HTTPS only). Check `GET /health/config` → `email_transcript_configured`.

## Cursor / tokens

Keep private corpora out of the workspace; use `.cursorignore` (see PDF). Agents should edit **code**, not bulk-read PDFs.

## Embedding dimensions

Use **one** embedding stack consistently: OpenAI embeddings (e.g. 1536-dim) **or** Gemini `text-embedding-004` (768-dim). The ingest script probes dimension on first embedding and creates the Qdrant collection accordingly.
