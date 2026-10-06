"""Thin client for the AnythingLLM Developer API (/api/v1).

AnythingLLM is our ingestion + vector store layer: it chunks, embeds and indexes
documents per workspace. We use one workspace per crawled site and call
`vector-search` to retrieve evidence; Claude does the reasoning on top.
"""
from __future__ import annotations

import logging

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import settings

log = logging.getLogger(__name__)


class AnythingLLMError(RuntimeError):
    pass


_retry = retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, max=10),
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
)


class AnythingLLM:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, client: httpx.Client | None = None):
        base = (base_url or settings.anythingllm_base_url).rstrip("/")
        key = api_key or settings.anythingllm_api_key
        if not key:
            raise AnythingLLMError("ANYTHINGLLM_API_KEY is not set (create one in AnythingLLM → Settings → Developer API)")
        self.http = client or httpx.Client(
            base_url=f"{base}/api/v1",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            timeout=120.0,  # embedding large docs can take a while
        )

    @_retry
    def _req(self, method: str, path: str, **kw) -> dict:
        r = self.http.request(method, path, **kw)
        if r.status_code in (429, 500, 502, 503, 504):
            r.raise_for_status()
        if r.status_code == 403:
            raise AnythingLLMError("AnythingLLM rejected the API key (403)")
        if r.status_code >= 400:
            raise AnythingLLMError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    # ---- health ------------------------------------------------------------

    def ping(self) -> bool:
        try:
            return bool(self._req("GET", "/auth").get("authenticated"))
        except Exception:
            return False

    # ---- workspaces --------------------------------------------------------

    def get_or_create_workspace(self, name: str) -> str:
        """Returns the workspace slug."""
        for ws in self._req("GET", "/workspaces").get("workspaces", []):
            if ws.get("name") == name:
                return ws["slug"]
        data = self._req("POST", "/workspace/new", json={"name": name, "similarityThreshold": 0.2, "topN": 6})
        ws = data.get("workspace")
        if not ws:
            raise AnythingLLMError(f"Could not create workspace {name}: {data.get('message')}")
        log.info("created AnythingLLM workspace %s", ws["slug"])
        return ws["slug"]

    def update_embeddings(self, slug: str, adds: list[str] | None = None, deletes: list[str] | None = None) -> None:
        self._req("POST", f"/workspace/{slug}/update-embeddings", json={"adds": adds or [], "deletes": deletes or []})

    # ---- documents ---------------------------------------------------------

    def add_text_document(self, slug: str, text: str, title: str, url: str, description: str = "") -> str:
        """Upload raw text and embed it into the workspace in one call. Returns the document location."""
        data = self._req(
            "POST",
            "/document/raw-text",
            json={
                "textContent": text,
                "addToWorkspaces": slug,
                "metadata": {
                    "title": title[:200] or url,
                    "url": url,
                    "docSource": url,
                    "chunkSource": url,
                    "description": description[:300],
                },
            },
        )
        docs = data.get("documents") or []
        if not data.get("success") or not docs:
            raise AnythingLLMError(f"raw-text upload failed for {url}: {data.get('error')}")
        return docs[0]["location"]

    # ---- retrieval ---------------------------------------------------------

    def vector_search(self, slug: str, query: str, top_n: int | None = None, threshold: float | None = None) -> list[dict]:
        data = self._req(
            "POST",
            f"/workspace/{slug}/vector-search",
            json={
                "query": query,
                "topN": top_n or settings.retrieval_top_n,
                "scoreThreshold": threshold if threshold is not None else settings.retrieval_score_threshold,
            },
        )
        return data.get("results", [])
