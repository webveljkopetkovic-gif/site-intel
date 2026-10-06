"""Polite same-site crawler.

- Seeds from the start URL + sitemap.xml
- Respects robots.txt
- Bounded concurrency, delay between requests, retries with backoff
- JS-rendered sites (React/Vite SPAs): if the raw HTML has almost no text,
  the page is re-rendered with a headless browser (Playwright) when available.
"""
from __future__ import annotations

import asyncio
import logging
import re
from urllib import robotparser
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import settings
from .extractor import extract, is_same_site, normalize_url, site_key
from .models import PageData, RawPage, SiteCrawl

log = logging.getLogger(__name__)


class BrowserRenderer:
    """Lazy Playwright wrapper. Optional dependency: `pip install playwright && playwright install chromium`."""

    def __init__(self) -> None:
        self._pw = self._browser = None
        self.available: bool | None = None

    async def render(self, url: str) -> str | None:
        if self.available is False:
            return None
        if self._browser is None:
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                log.warning("Playwright not installed: JS-rendered pages will be crawled as raw HTML only")
                self.available = False
                return None
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch()
            self.available = True
        page = await self._browser.new_page(user_agent=settings.user_agent)
        try:
            await page.goto(url, wait_until="networkidle", timeout=settings.request_timeout_s * 1000)
            return await page.content()
        finally:
            await page.close()

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()


def _visible_text_len(html: str) -> int:
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "template"]):
        t.decompose()
    return len((soup.body or soup).get_text(" ", strip=True))


class Crawler:
    def __init__(self, start_url: str, max_pages: int | None = None) -> None:
        self.start_url = normalize_url(start_url, start_url) or start_url
        self.max_pages = max_pages or settings.max_pages_per_site
        self.robots = robotparser.RobotFileParser()
        self.renderer = BrowserRenderer()
        self.client = httpx.AsyncClient(
            headers={"User-Agent": settings.user_agent, "Accept-Language": "en,sr;q=0.8,ru;q=0.6"},
            timeout=settings.request_timeout_s,
            follow_redirects=True,
        )

    # ---- network ---------------------------------------------------------

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=8),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    )
    async def _get(self, url: str) -> httpx.Response:
        r = await self.client.get(url)
        if r.status_code in (429, 500, 502, 503, 504):
            r.raise_for_status()  # -> retried with backoff
        return r

    async def _load_robots(self) -> None:
        p = urlparse(self.start_url)
        try:
            r = await self.client.get(f"{p.scheme}://{p.netloc}/robots.txt")
            self.robots.parse(r.text.splitlines() if r.status_code == 200 else [])
        except httpx.HTTPError:
            self.robots.parse([])

    async def _sitemap_urls(self) -> list[str]:
        p = urlparse(self.start_url)
        candidates = list(getattr(self.robots, "site_maps", lambda: None)() or []) or [f"{p.scheme}://{p.netloc}/sitemap.xml"]
        urls: list[str] = []
        for sm in candidates[:3]:
            try:
                r = await self.client.get(sm)
                if r.status_code == 200 and "<urlset" in r.text:
                    urls += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)
            except httpx.HTTPError:
                continue
        return [u for u in (normalize_url(x, self.start_url) for x in urls) if u and is_same_site(u, self.start_url)]

    async def _fetch(self, url: str) -> RawPage | None:
        try:
            r = await self._get(url)
        except httpx.HTTPError as e:
            log.warning("fetch failed %s: %s", url, e)
            return None
        if "text/html" not in r.headers.get("content-type", ""):
            return None
        raw = RawPage(url=str(r.url), status=r.status_code, html=r.text)
        mode = settings.render_js
        if mode == "always" or (mode == "auto" and _visible_text_len(r.text) < settings.min_text_chars):
            try:
                html = await self.renderer.render(str(r.url))
                if html:
                    raw = RawPage(url=str(r.url), status=r.status_code, html=html, rendered_with="browser")
            except Exception as e:  # browser failures must never kill the crawl
                log.warning("browser render failed %s: %s", url, e)
        return raw

    # ---- main loop --------------------------------------------------------

    async def run(self) -> SiteCrawl:
        await self._load_robots()
        queue: asyncio.Queue[str] = asyncio.Queue()
        seen: set[str] = set()
        pages: list[PageData] = []

        def enqueue(u: str) -> None:
            if u not in seen and len(seen) < self.max_pages * 3 and self.robots.can_fetch(settings.user_agent, u):
                seen.add(u)
                queue.put_nowait(u)

        enqueue(self.start_url)
        for u in await self._sitemap_urls():
            enqueue(u)

        async def worker() -> None:
            while True:
                url = await queue.get()
                try:
                    if len(pages) >= self.max_pages:
                        continue
                    raw = await self._fetch(url)
                    if not raw:
                        continue
                    final = normalize_url(raw.url, raw.url)
                    if final and final != url and any(p.url == raw.url for p in pages):
                        continue  # redirect landed on a page we already have
                    page = extract(raw)
                    pages.append(page)
                    log.info("[%d/%d] %s (%s, %d words)", len(pages), self.max_pages, url, raw.rendered_with, page.word_count)
                    for link in page.internal_links:
                        enqueue(link)
                    await asyncio.sleep(settings.request_delay_s)
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(settings.crawl_concurrency)]
        try:
            await queue.join()
        finally:
            for w in workers:
                w.cancel()
            await self.client.aclose()
            await self.renderer.close()

        pages.sort(key=lambda p: (p.url.count("/"), p.url))
        return SiteCrawl(site=site_key(self.start_url), start_url=self.start_url, pages=pages[: self.max_pages])
