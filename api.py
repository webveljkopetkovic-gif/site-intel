"""REST API + minimal UI.

Run:  uvicorn app.api:app --reload --port 8000     then open http://localhost:8000
Docs: http://localhost:8000/docs  (auto-generated OpenAPI)
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field, HttpUrl

from .analyzer import Analyzer
from .anythingllm import AnythingLLM
from .config import settings
from .extractor import site_key
from .models import AskAnswer, SiteReport
from .pipeline import list_sites, load_report, run_site, workspace_name
from .report import render_html

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")

app = FastAPI(title="Site Intel", version="1.0", description="Crawl → extract → AnythingLLM RAG → Claude analysis → reports")
STATIC = Path(__file__).parent / "static"

JOBS: dict[str, dict] = {}  # in-memory job registry (swap for Redis/DB in production)


def auth(authorization: str | None = Header(default=None)) -> None:
    if settings.api_token and authorization != f"Bearer {settings.api_token}":
        raise HTTPException(401, "Invalid or missing API token")


class RunRequest(BaseModel):
    url: HttpUrl
    max_pages: int = Field(default=settings.max_pages_per_site, ge=1, le=500)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


def _run_job(job_id: str, url: str, max_pages: int) -> None:
    job = JOBS[job_id]
    job["status"] = "running"

    def progress(msg: str) -> None:
        log.info("[%s] %s", job_id[:8], msg)
        job["log"].append(msg)

    try:
        report = asyncio.run(run_site(url, max_pages, progress))
        job.update(status="done", site=report.site)
    except Exception as e:
        log.exception("job %s failed", job_id)
        job.update(status="failed", error=str(e))


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def ui() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health() -> dict:
    try:
        llm_ok = AnythingLLM().ping()
    except Exception:
        llm_ok = False
    return {"ok": True, "anythingllm": llm_ok, "claude_key_set": bool(settings.anthropic_api_key), "model": settings.claude_model}


@app.post("/runs", dependencies=[Depends(auth)], status_code=202)
def start_run(req: RunRequest, bg: BackgroundTasks) -> dict:
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"id": job_id, "url": str(req.url), "site": site_key(str(req.url)), "status": "queued", "log": [], "error": None}
    bg.add_task(_run_job, job_id, str(req.url), req.max_pages)
    return JOBS[job_id]


@app.get("/runs/{job_id}", dependencies=[Depends(auth)])
def get_run(job_id: str) -> dict:
    if job_id not in JOBS:
        raise HTTPException(404, "Unknown job")
    return JOBS[job_id]


@app.get("/sites", dependencies=[Depends(auth)])
def sites() -> list[dict]:
    out = []
    for s in list_sites():
        loaded = load_report(s)
        if loaded:
            r, c = loaded
            out.append({"site": s, "pages": len(c.pages), "generated_at": r.generated_at,
                        "avg_score": round(sum(x.score for x in r.sections) / max(1, len(r.sections)), 1)})
    return out


@app.get("/sites/{site}/report", dependencies=[Depends(auth)], response_model=SiteReport)
def report_json(site: str) -> SiteReport:
    loaded = load_report(site)
    if not loaded:
        raise HTTPException(404, "No report for this site yet")
    return loaded[0]


@app.get("/sites/{site}/report.html", response_class=HTMLResponse, dependencies=[Depends(auth)])
def report_html(site: str) -> str:
    loaded = load_report(site)
    if not loaded:
        raise HTTPException(404, "No report for this site yet")
    return render_html(*loaded)


@app.get("/sites/{site}/crawl", dependencies=[Depends(auth)])
def crawl_json(site: str) -> FileResponse:
    f = settings.data_dir / site / "crawl.json"
    if not f.exists():
        raise HTTPException(404, "No crawl for this site yet")
    return FileResponse(f, media_type="application/json")


@app.post("/sites/{site}/ask", dependencies=[Depends(auth)], response_model=AskAnswer)
def ask(site: str, req: AskRequest) -> AskAnswer:
    if not (settings.data_dir / site / "manifest.json").exists():
        raise HTTPException(404, "This site hasn't been ingested yet")
    llm = AnythingLLM()
    slug = llm.get_or_create_workspace(workspace_name(site))
    return Analyzer(llm).ask(slug, req.question)
