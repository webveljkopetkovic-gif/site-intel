# Site Intel — crawl → extract → AnythingLLM RAG → Claude analysis → reports

A small, production-shaped pipeline that turns any website into a structured, evidence-backed audit report and a Q&A endpoint.

```
                ┌──────────────┐   ┌─────────────────┐   ┌────────────────────┐   ┌──────────────────┐   ┌──────────────────┐
 sites.yaml ──▶ │ 1. Crawler   │──▶│ 2. Extractor    │──▶│ 3. AnythingLLM     │──▶│ 4. Claude API    │──▶│ 5. Reports / API │
 or API call    │ robots.txt,  │   │ title, meta,    │   │ raw-text upload,   │   │ retrieval-ground-│   │ HTML report,     │
                │ sitemap, JS  │   │ headings, CTAs, │   │ embed per-site     │   │ ed audit, forced │   │ JSON, REST API,  │
                │ rendering,   │   │ contacts, schema│   │ workspace, vector  │   │ into a JSON      │   │ simple web UI,   │
                │ retries      │   │ + rule-based    │   │ search             │   │ schema           │   │ RAG Q&A          │
                └──────────────┘   │ SEO issues      │   └────────────────────┘   └──────────────────┘   └──────────────────┘
                                   └─────────────────┘
          data/<site>/crawl.json        manifest.json (incremental)          report.json + report.html
```

## What each part does

| File | Job |
|---|---|
| `app/crawler.py` | Async same-site crawler. Seeds from the homepage + `sitemap.xml`, obeys `robots.txt`, limits concurrency, delays between requests, retries 429/5xx with backoff. **React/Vite SPAs** (empty `<div id="root">`) are re-rendered in headless Chromium via Playwright automatically. |
| `app/extractor.py` | Pure HTML → `PageData`: title, meta, canonical, lang, OG tags, H1–H3, clean main text (nav/footer stripped), internal/external links, CTAs (EN/SR/RU), emails, phones, image alt coverage, JSON-LD types, plus deterministic SEO issues. |
| `app/anythingllm.py` | Client for the AnythingLLM Developer API: workspaces, `document/raw-text` upload + embed, `update-embeddings`, `vector-search`. Retries, clear errors. |
| `app/pipeline.py` | Orchestration. One AnythingLLM workspace per site. **Incremental ingest**: unchanged pages (content hash) are skipped, changed pages are swapped, removed pages are un-embedded. Every stage persists to `data/<site>/`. |
| `app/analyzer.py` | Retrieves evidence from AnythingLLM for each audit dimension, sends facts + evidence to Claude, and **forces the output into a JSON schema** (structured outputs), then validates it with Pydantic. Also does cited RAG Q&A. |
| `app/report.py` | Standalone printable HTML report (scores, top priorities, findings with evidence URLs, page inventory). |
| `app/api.py` | FastAPI: start runs as background jobs, poll status, fetch reports (HTML/JSON), ask questions. OpenAPI docs at `/docs`. Optional bearer-token auth. |
| `app/cli.py` | Same pipeline from the terminal. |
| `tests/` | 13 offline tests — websites, AnythingLLM and Claude are all mocked. |

## Run it on Windows (PowerShell)

**Prerequisites:** Python 3.11+, Docker Desktop, an Anthropic API key.

```powershell
# 1. Start AnythingLLM (local embeddings + LanceDB, no extra keys)
cd site-intel
Copy-Item .env.example .env        # then open .env and paste ANTHROPIC_API_KEY
docker compose up -d anythingllm
# Open http://localhost:3001 → finish onboarding → Settings → Developer API → "Generate new API key"
# Paste that key into .env as ANYTHINGLLM_API_KEY

# 2. Python environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
playwright install chromium        # needed for the React/Vite sites

# 3. Tests (offline)
pytest -q

# 4. Run the pipeline on the sites in sites.yaml
python -m app.cli run
#   → data\vdigital.dev\report.html  (open it in the browser)

# 5. Ask questions (RAG)
python -m app.cli ask vdigital.dev "What services are offered and how do I get in touch?"

# 6. API + UI
uvicorn app.api:app --reload --port 8000
#   → http://localhost:8000   (UI)     http://localhost:8000/docs   (API)
```

Everything in Docker instead: `docker compose up -d --build` → UI on `http://localhost:8000`.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | API up, AnythingLLM reachable, Claude key set |
| `POST` | `/runs` `{"url": "...", "max_pages": 25}` | Start a crawl→report job (returns job id) |
| `GET` | `/runs/{id}` | Job status + progress log |
| `GET` | `/sites` | Sites with reports + average score |
| `GET` | `/sites/{site}/report` · `/report.html` · `/crawl` | Report JSON / HTML / raw extraction |
| `POST` | `/sites/{site}/ask` `{"question": "..."}` | Cited answer from the site's indexed pages |

## Design decisions (good to mention on a call)

- **AnythingLLM as the ingestion/vector layer, Claude as the reasoning layer.** The pipeline uses AnythingLLM's `vector-search` for retrieval instead of its chat endpoint, so prompts, citations and output format stay under our control.
- **Structured output, not free text.** Claude's report must match a JSON schema; it's validated before anything is saved, so the API and UI never parse prose.
- **Grounding.** Claude sees deterministic facts for every page plus retrieved chunks and must attach an evidence URL to each finding. It's instructed not to invent clients or numbers.
- **Incremental + resumable.** Content hashes avoid re-embedding unchanged pages; a manifest is checkpointed after each document.
- **Polite crawling.** robots.txt, sitemap, rate limit, retries, and JS rendering only when needed (cheaper and faster).
- **Multilingual.** CTA detection and retrieval queries cover English, Serbian and Russian. For SR/RU-heavy sites, switch AnythingLLM's embedder to a multilingual model in its settings for better retrieval.

## Production next steps (milestone 2+)

Job queue (Redis/RQ or Celery) instead of in-memory jobs · Postgres for runs and history · scheduled re-crawls with change diffs · per-client auth and rate limits · PDF export · cost tracking per run (token usage) · Sentry/logging · deploy to a VPS (Hetzner/DigitalOcean) behind Caddy with HTTPS.

## Before showing it publicly

Only run it on sites you own or have permission to analyze. The client site in `sites.yaml` is disabled by default.
