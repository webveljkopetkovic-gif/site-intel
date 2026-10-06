"""Typed data structures that flow between pipeline stages."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RawPage(BaseModel):
    url: str
    status: int
    html: str
    rendered_with: Literal["http", "browser"] = "http"
    fetched_at: datetime = Field(default_factory=utcnow)


class PageData(BaseModel):
    """Structured extraction result for a single page."""

    url: str
    status: int
    title: str = ""
    meta_description: str = ""
    canonical: str = ""
    lang: str = ""
    og: dict[str, str] = {}
    h1: list[str] = []
    h2: list[str] = []
    h3: list[str] = []
    text: str = ""  # clean main-content text (markdown-ish)
    word_count: int = 0
    internal_links: list[str] = []
    external_links: list[str] = []
    ctas: list[str] = []
    emails: list[str] = []
    phones: list[str] = []
    images_total: int = 0
    images_missing_alt: int = 0
    has_json_ld: bool = False
    json_ld_types: list[str] = []
    issues: list[str] = []  # deterministic SEO/quality issues found by rules
    content_hash: str = ""
    rendered_with: str = "http"


class SiteCrawl(BaseModel):
    site: str  # normalized host, e.g. vdigital.dev
    start_url: str
    pages: list[PageData]
    crawled_at: datetime = Field(default_factory=utcnow)


# ---------- Claude analysis output (enforced via tool-use JSON schema) ----------


class Finding(BaseModel):
    text: str
    evidence_url: str = ""


class Section(BaseModel):
    name: str
    score: int = Field(ge=1, le=10)
    findings: list[Finding]
    recommendations: list[str]


class Priority(BaseModel):
    title: str
    why: str
    impact: Literal["low", "medium", "high"]
    effort: Literal["low", "medium", "high"]


class SiteReport(BaseModel):
    site: str
    summary: str
    audience_guess: str
    sections: list[Section]
    top_priorities: list[Priority]
    model: str = ""
    generated_at: datetime = Field(default_factory=utcnow)
    pages_analyzed: int = 0
    chunks_retrieved: int = 0


class AskAnswer(BaseModel):
    answer: str
    sources: list[dict]
