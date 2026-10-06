# Loom demo script (3–4 minutes)

**0:00 — Intro (15s)**
"Hi, I'm Veljko. This is a pipeline I built that matches your spec: crawl a website, extract structured data, ingest into AnythingLLM, analyze with the Claude API, and serve reports through an API and a simple UI."

**0:15 — Architecture (30s)** Show the diagram at the top of README.md.
"Five stages. Each one writes its output to disk, so a run can be inspected or resumed."

**0:45 — Live run (60s)** Open http://localhost:8000, paste https://vdigital.dev, click *Run pipeline*.
While it runs: "The crawler respects robots.txt and the sitemap. This site is a React app, so the raw HTML is empty. The crawler detects that and renders it in a headless browser automatically."

**1:45 — AnythingLLM (30s)** Switch to http://localhost:3001 and show the workspace `site-vdigital-dev` with the embedded pages.
"One workspace per site. Re-runs are incremental: unchanged pages are skipped, changed ones are swapped."

**2:15 — Report (45s)** Open the report.
"Claude's output is forced into a JSON schema and validated, so it's reliable for an API. Every finding links to the page it came from."

**3:00 — RAG Q&A + API (30s)** Ask: "How do I contact them and what services do they offer?" Show the cited sources. Then open /docs.

**3:30 — Close (15s)**
"There are 13 offline tests and a Docker Compose setup. Next steps for production would be a job queue, Postgres, and scheduled re-crawls."

---

# Updated answers for Soné (fill the brackets)

1. **RAG / document pipeline:** "I built a working crawl → extract → AnythingLLM → Claude pipeline that mirrors this project. Here's a 3-minute walkthrough: [Loom link]. Code available on request / [GitHub link]." *(Only add a client production example if you have a real one.)*
2. **AnythingLLM:** "Yes. Self-hosted via Docker, used through its Developer API (raw-text ingestion, per-site workspaces, vector-search for retrieval)."
3. **Claude API:** "Yes. Structured JSON outputs validated with Pydantic, retrieval-grounded prompts, and cited Q&A." *(Add a production project here only if real.)*
4. **Links:** Loom demo · repo · www.veljkopetkovic.com · vdigital.dev
5. **Milestone 1** as in the earlier draft.
