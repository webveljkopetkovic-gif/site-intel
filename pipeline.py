"""Orchestrates: crawl -> extract -> ingest (AnythingLLM) -> analyze (Claude) -> report.

Every stage writes its output to data/<site>/ so a run can be inspected, resumed
or re-analyzed without re-crawling. Ingestion is incremental: pages whose content
hash didn't change since the last run are not re-embedded; changed or removed
pages are swapped out of the workspace.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from .analyzer import Analyzer
from .anythingllm import AnythingLLM
from .config import settings
from .crawler import Crawler
from .extractor import site_key, to_markdown
from .models import SiteCrawl, SiteReport
from .report import render_html

log = logging.getLogger(__name__)
Progress = Callable[[str], None]


def site_dir(site: str) -> Path:
    d = settings.data_dir / site
    d.mkdir(parents=True, exist_ok=True)
    return d


def workspace_name(site: str) -> str:
    return f"{settings.workspace_prefix}-{site.replace('.', '-')}"


def ingest(crawl: SiteCrawl, llm: AnythingLLM, progress: Progress = log.info) -> tuple[str, dict]:
    slug = llm.get_or_create_workspace(workspace_name(crawl.site))
    manifest_path = site_dir(crawl.site) / "manifest.json"
    manifest: dict[str, dict] = json.loads(manifest_path.read_text("utf-8")) if manifest_path.exists() else {}
    current_urls = {p.url for p in crawl.pages}
    stats = {"added": 0, "updated": 0, "unchanged": 0, "removed": 0, "skipped_empty": 0}

    stale = [u for u in manifest if u not in current_urls]
    if stale:
        llm.update_embeddings(slug, deletes=[manifest[u]["location"] for u in stale])
        for u in stale:
            manifest.pop(u)
        stats["removed"] = len(stale)

    for page in crawl.pages:
        if page.word_count < 20:
            stats["skipped_empty"] += 1
            continue
        prev = manifest.get(page.url)
        if prev and prev["hash"] == page.content_hash:
            stats["unchanged"] += 1
            continue
        if prev:
            llm.update_embeddings(slug, deletes=[prev["location"]])
        location = llm.add_text_document(slug, to_markdown(page), page.title, page.url, page.meta_description)
        manifest[page.url] = {"hash": page.content_hash, "location": location}
        stats["updated" if prev else "added"] += 1
        progress(f"embedded {page.url}")
        manifest_path.write_text(json.dumps(manifest, indent=2), "utf-8")  # checkpoint after each doc

    manifest_path.write_text(json.dumps(manifest, indent=2), "utf-8")
    return slug, stats


async def run_site(url: str, max_pages: int | None = None, progress: Progress = log.info) -> SiteReport:
    site = site_key(url)
    out = site_dir(site)

    progress(f"crawling {url}")
    crawl = await Crawler(url, max_pages).run()
    if not crawl.pages:
        raise RuntimeError(f"No pages could be fetched from {url}")
    (out / "crawl.json").write_text(crawl.model_dump_json(indent=2), "utf-8")
    progress(f"crawled {len(crawl.pages)} pages")

    llm = AnythingLLM()
    progress("ingesting into AnythingLLM")
    slug, stats = ingest(crawl, llm, progress)
    progress(f"ingest done: {stats}")

    progress("analyzing with Claude")
    report = Analyzer(llm).analyze(crawl, slug)
    (out / "report.json").write_text(report.model_dump_json(indent=2), "utf-8")
    (out / "report.html").write_text(render_html(report, crawl), "utf-8")
    progress(f"report written to {out / 'report.html'}")
    return report


def load_report(site: str) -> tuple[SiteReport, SiteCrawl] | None:
    d = settings.data_dir / site
    if not (d / "report.json").exists():
        return None
    return (
        SiteReport.model_validate_json((d / "report.json").read_text("utf-8")),
        SiteCrawl.model_validate_json((d / "crawl.json").read_text("utf-8")),
    )


def list_sites() -> list[str]:
    if not settings.data_dir.exists():
        return []
    return sorted(p.name for p in settings.data_dir.iterdir() if (p / "report.json").exists())
