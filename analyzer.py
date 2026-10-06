"""Claude analysis layer.

Retrieval (AnythingLLM vector-search) gives Claude grounded evidence; Claude returns
a report that is *forced* into a JSON schema via structured outputs, then validated
with Pydantic, so downstream code (API, UI, exports) never has to parse free text.
"""
from __future__ import annotations

import copy
import json
import logging
from typing import Literal

import anthropic
from pydantic import BaseModel, ValidationError

from .anythingllm import AnythingLLM
from .config import settings
from .models import AskAnswer, Finding, Priority, Section, SiteCrawl, SiteReport

log = logging.getLogger(__name__)

# What we ask the vector store for, per audit dimension. Multilingual on purpose:
# the demo sites are in English, Serbian and Russian.
RETRIEVAL_QUERIES: dict[str, list[str]] = {
    "Positioning & offer": [
        "what the business offers and who it is for, value proposition",
        "usluge i ponuda, za koga je",
        "услуги и предложение, для кого",
    ],
    "Services, pricing & process": ["services packages pricing process steps how we work", "cene paketi proces", "цены программы процесс"],
    "Trust & proof": ["testimonials case studies results clients portfolio experience about the team", "iskustvo rezultati klijenti", "отзывы результаты опыт"],
    "Conversion & contact": ["contact book a call get a quote form whatsapp telegram email", "kontakt zakaži poziv", "контакты записаться связаться"],
}

SYSTEM_PROMPT = """You are a senior web strategist and technical SEO auditor writing for a business owner.
You receive (1) deterministic facts extracted from every crawled page and (2) evidence chunks
retrieved from a vector index of the site. Rules:
- Ground every finding in the provided facts or evidence. Put the page URL it comes from in evidence_url.
- Never invent clients, numbers, or pages that are not in the input. If something is missing, say it is missing.
- The site may be in English, Serbian or Russian; always write the report in clear English.
- Scores are 1-10 where 10 = best practice. Be honest, specific and actionable; no filler.
- Sections to produce, in this order: Positioning & offer, Services, pricing & process, Trust & proof,
  Conversion & contact, Technical SEO, Content quality.
- top_priorities: the 3-5 changes with the best impact/effort ratio, most valuable first."""


# ---- LLM output schema (strict: everything required, no defaults) ----


class _Finding(BaseModel):
    text: str
    evidence_url: str


class _Section(BaseModel):
    name: str
    score: int
    findings: list[_Finding]
    recommendations: list[str]


class _Priority(BaseModel):
    title: str
    why: str
    impact: Literal["low", "medium", "high"]
    effort: Literal["low", "medium", "high"]


class ReportDraft(BaseModel):
    summary: str
    audience_guess: str
    sections: list[_Section]
    top_priorities: list[_Priority]


def strict_schema(model: type[BaseModel]) -> dict:
    """Pydantic JSON schema -> self-contained strict schema (refs inlined, closed objects)."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            node = {k: resolve(v) for k, v in node.items() if k != "title"}
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            return node
        if isinstance(node, list):
            return [resolve(x) for x in node]
        return node

    return resolve(schema)


# ---- helpers ----


def _facts_block(crawl: SiteCrawl) -> str:
    lines = [f"SITE: {crawl.site}  (start: {crawl.start_url}, pages crawled: {len(crawl.pages)})", ""]
    for p in crawl.pages:
        lines += [
            f"PAGE {p.url}  [HTTP {p.status}, {p.word_count} words, lang={p.lang or '-'}, rendered={p.rendered_with}]",
            f"  title: {p.title or '-'}",
            f"  meta: {p.meta_description or '-'}",
            f"  h1: {' | '.join(p.h1) or '-'}",
            f"  h2: {' | '.join(p.h2[:12]) or '-'}",
            f"  ctas: {', '.join(p.ctas[:10]) or '-'}",
            f"  contact: {', '.join(p.emails + p.phones) or '-'}",
            f"  schema: {', '.join(p.json_ld_types) or '-'}  og:image: {'yes' if p.og.get('og:image') else 'no'}",
            f"  rule-based issues: {'; '.join(p.issues) or 'none'}",
        ]
    return "\n".join(lines)


def _evidence_block(chunks: list[dict], max_chars: int = 40_000) -> str:
    out, used = [], 0
    for i, c in enumerate(chunks, 1):
        url = (c.get("metadata") or {}).get("url") or (c.get("metadata") or {}).get("docSource", "")
        piece = f"[{i}] ({c.get('_dimension', '')}) source: {url}\n{c.get('text', '').strip()}\n"
        if used + len(piece) > max_chars:
            break
        out.append(piece)
        used += len(piece)
    return "\n".join(out)


class Analyzer:
    def __init__(self, llm: AnythingLLM, client: anthropic.Anthropic | None = None, model: str | None = None):
        self.llm = llm
        self.model = model or settings.claude_model
        self.client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key or None, max_retries=4)

    def retrieve(self, slug: str) -> list[dict]:
        seen, chunks = set(), []
        for dimension, queries in RETRIEVAL_QUERIES.items():
            for q in queries:
                for c in self.llm.vector_search(slug, q):
                    key = c.get("id") or c.get("text", "")[:120]
                    if key not in seen:
                        seen.add(key)
                        chunks.append({**c, "_dimension": dimension})
        return chunks

    def analyze(self, crawl: SiteCrawl, slug: str) -> SiteReport:
        chunks = self.retrieve(slug)
        home = crawl.pages[0].text[:4000] if crawl.pages else ""
        user = (
            f"## Extracted facts\n{_facts_block(crawl)}\n\n"
            f"## Homepage text (first 4000 chars)\n{home}\n\n"
            f"## Retrieved evidence ({len(chunks)} chunks from AnythingLLM)\n{_evidence_block(chunks)}\n\n"
            "Write the audit report."
        )
        log.info("analyzing %s with %s (%d chunks, ~%d chars)", crawl.site, self.model, len(chunks), len(user))
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=settings.claude_max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": strict_schema(ReportDraft)}},
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        try:
            draft = ReportDraft.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError) as e:
            raise RuntimeError(f"Claude returned output that does not match the report schema: {e}") from e

        return SiteReport(
            site=crawl.site,
            summary=draft.summary,
            audience_guess=draft.audience_guess,
            sections=[
                Section(
                    name=s.name,
                    score=min(10, max(1, s.score)),
                    findings=[Finding(**f.model_dump()) for f in s.findings],
                    recommendations=s.recommendations,
                )
                for s in draft.sections
            ],
            top_priorities=[Priority(**p.model_dump()) for p in draft.top_priorities],
            model=self.model,
            pages_analyzed=len(crawl.pages),
            chunks_retrieved=len(chunks),
        )

    def ask(self, slug: str, question: str) -> AskAnswer:
        """Classic RAG Q&A: retrieve from AnythingLLM, answer with Claude, cite sources."""
        chunks = self.llm.vector_search(slug, question, top_n=8)
        if not chunks:
            return AskAnswer(answer="I couldn't find anything relevant in the indexed pages for that question.", sources=[])
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=1500,
            system=(
                "Answer only from the numbered sources. Cite them inline like [1]. "
                "If the sources don't contain the answer, say so plainly. Answer in the language of the question."
            ),
            messages=[{"role": "user", "content": f"Sources:\n{_evidence_block(chunks, 25_000)}\n\nQuestion: {question}"}],
        )
        answer = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        sources = [
            {"n": i, "url": (c.get("metadata") or {}).get("url", ""), "title": (c.get("metadata") or {}).get("title", ""), "score": c.get("score")}
            for i, c in enumerate(chunks, 1)
        ]
        return AskAnswer(answer=answer, sources=sources)
