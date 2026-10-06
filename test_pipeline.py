"""Offline tests: every external service (websites, AnythingLLM, Claude) is mocked."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import respx

from app import pipeline
from app.analyzer import Analyzer, ReportDraft, strict_schema
from app.anythingllm import AnythingLLM
from app.config import settings
from app.crawler import Crawler
from app.extractor import extract, normalize_url, site_key, to_markdown
from app.models import RawPage

HOME = """<!doctype html><html lang="en"><head><title>Acme Studio – Web apps for clinics</title>
<meta name="description" content="We build fast websites and booking apps for private clinics in Serbia and the EU.">
<link rel="canonical" href="https://acme.test/">
<meta property="og:image" content="https://acme.test/og.png">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization","name":"Acme"}</script>
</head><body><nav><a href="/">Home</a><a href="/services/">Services</a></nav>
<main><h1>Websites that fill your calendar</h1><p>We design and build booking-ready websites for clinics. """ + "Clear offer and proof. " * 40 + """</p>
<h2>Why us</h2><p>Contact: hello@acme.test or +381 65 123 4567</p>
<a class="btn" href="/contact?utm_source=x">Book a call</a><a href="https://instagram.com/acme">IG</a>
<img src="a.png" alt="team"><img src="b.png"></main><footer>© Acme</footer></body></html>"""

SERVICES = """<html><head><title>Services</title></head><body><main><h1>Services</h1><p>""" + "Websites, booking apps, SEO. " * 30 + """</p>
<a href="/">home</a><a href="/files/price.pdf">pdf</a></main></body></html>"""

CONTACT = "<html><head><title>Contact</title></head><body><h1>Contact</h1><p>Write to us.</p></body></html>"

SPA_SHELL = '<html><head><title>App</title></head><body><div id="root"></div><script src="/assets/index.js"></script></body></html>'


# ------------------------------------------------------------------ extractor


def test_normalize_url():
    assert normalize_url("/a/?utm_source=x&id=2#top", "https://Acme.test/") == "https://acme.test/a?id=2"
    assert normalize_url("mailto:x@y.z", "https://acme.test") is None
    assert normalize_url("/doc.pdf", "https://acme.test") is None
    assert site_key("https://www.Acme.test/x") == "acme.test"


def test_extract_home():
    p = extract(RawPage(url="https://acme.test/", status=200, html=HOME))
    assert p.title.startswith("Acme Studio")
    assert p.h1 == ["Websites that fill your calendar"]
    assert "Book a call" in p.ctas
    assert "hello@acme.test" in p.emails and any("381" in x for x in p.phones)
    assert p.json_ld_types == ["Organization"] and p.has_json_ld
    assert p.images_total == 2 and p.images_missing_alt == 1
    assert "https://acme.test/services" in p.internal_links and "https://acme.test/contact" in p.internal_links
    assert "https://instagram.com/acme" in p.external_links
    assert "Home" not in p.text.splitlines()[0]  # nav stripped
    assert "# Websites that fill your calendar" in p.text
    assert any("missing alt" in i for i in p.issues)
    assert not any("H1" in i for i in p.issues)


def test_extract_flags_problems():
    p = extract(RawPage(url="https://acme.test/contact", status=200, html=CONTACT))
    joined = " ".join(p.issues)
    for expected in ("meta description", "canonical", "Thin content", "JSON-LD", "lang"):
        assert expected in joined
    md = to_markdown(p)
    assert md.startswith("# Contact") and "URL: https://acme.test/contact" in md


# ------------------------------------------------------------------ crawler


def _mock_site(router: respx.Router, home=HOME):
    router.get("https://acme.test/robots.txt").respond(200, text="User-agent: *\nDisallow: /private\n")
    router.get("https://acme.test/sitemap.xml").respond(
        200, text="<urlset><url><loc>https://acme.test/private/x</loc></url><url><loc>https://acme.test/services/</loc></url></urlset>"
    )
    html = {"content-type": "text/html; charset=utf-8"}
    router.get("https://acme.test/").respond(200, text=home, headers=html)
    router.get("https://acme.test/services").respond(200, text=SERVICES, headers=html)
    router.get("https://acme.test/contact").respond(200, text=CONTACT, headers=html)


@respx.mock
def test_crawler_respects_robots_and_dedupes(monkeypatch):
    monkeypatch.setattr(settings, "request_delay_s", 0)
    monkeypatch.setattr(settings, "render_js", "never")
    _mock_site(respx.mock)
    crawl = asyncio.run(Crawler("https://acme.test/", max_pages=10).run())
    urls = [p.url for p in crawl.pages]
    assert crawl.site == "acme.test"
    assert sorted(urls) == ["https://acme.test/", "https://acme.test/contact", "https://acme.test/services"]
    assert not any("private" in u for u in urls)


@respx.mock
def test_crawler_renders_spa_with_browser(monkeypatch):
    monkeypatch.setattr(settings, "request_delay_s", 0)
    monkeypatch.setattr(settings, "render_js", "auto")
    _mock_site(respx.mock, home=SPA_SHELL)

    async def fake_render(self, url):
        return HOME if url == "https://acme.test/" else None

    monkeypatch.setattr("app.crawler.BrowserRenderer.render", fake_render)
    crawl = asyncio.run(Crawler("https://acme.test/", max_pages=10).run())
    home = next(p for p in crawl.pages if p.url == "https://acme.test/")
    assert home.rendered_with == "browser" and home.word_count > 100


@respx.mock
def test_crawler_max_pages(monkeypatch):
    monkeypatch.setattr(settings, "request_delay_s", 0)
    monkeypatch.setattr(settings, "render_js", "never")
    _mock_site(respx.mock)
    assert len(asyncio.run(Crawler("https://acme.test/", max_pages=1).run()).pages) == 1


# ------------------------------------------------------------------ AnythingLLM client


class FakeLLM:
    """In-memory stand-in for AnythingLLM with the same public methods."""

    def __init__(self):
        self.docs, self.deleted, self.n = {}, [], 0

    def get_or_create_workspace(self, name):
        return name

    def add_text_document(self, slug, text, title, url, description=""):
        self.n += 1
        loc = f"custom-documents/raw-{self.n}.json"
        self.docs[loc] = {"text": text, "url": url, "title": title}
        return loc

    def update_embeddings(self, slug, adds=None, deletes=None):
        for d in deletes or []:
            self.deleted.append(d)
            self.docs.pop(d, None)

    def vector_search(self, slug, query, top_n=None, threshold=None):
        return [{"id": loc, "text": d["text"][:300], "score": 0.8, "metadata": {"url": d["url"], "title": d["title"]}} for loc, d in self.docs.items()]


@respx.mock
def test_anythingllm_client_calls():
    base = "http://allm.test/api/v1"
    respx.get(f"{base}/workspaces").respond(200, json={"workspaces": [{"name": "other", "slug": "other"}]})
    new = respx.post(f"{base}/workspace/new").respond(200, json={"workspace": {"slug": "site-acme-test"}, "message": None})
    raw = respx.post(f"{base}/document/raw-text").respond(
        200, json={"success": True, "error": None, "documents": [{"location": "custom-documents/raw-abc.json"}]}
    )
    vs = respx.post(f"{base}/workspace/site-acme-test/vector-search").respond(200, json={"results": [{"id": "1", "text": "hi"}]})

    c = AnythingLLM("http://allm.test", "key")
    slug = c.get_or_create_workspace("site-acme-test")
    assert slug == "site-acme-test" and new.called
    assert c.add_text_document(slug, "text", "T", "https://acme.test/") == "custom-documents/raw-abc.json"
    body = json.loads(raw.calls.last.request.content)
    assert body["addToWorkspaces"] == "site-acme-test" and body["metadata"]["title"] == "T"
    assert raw.calls.last.request.headers["Authorization"] == "Bearer key"
    assert c.vector_search(slug, "q")[0]["text"] == "hi" and vs.called


@respx.mock
def test_anythingllm_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda s: None)
    route = respx.get("http://allm.test/api/v1/workspaces")
    route.side_effect = [httpx.Response(503), httpx.Response(200, json={"workspaces": [{"name": "w", "slug": "w"}]})]
    assert AnythingLLM("http://allm.test", "key").get_or_create_workspace("w") == "w"
    assert route.call_count == 2


# ------------------------------------------------------------------ ingest (incremental)


def _crawl(*pages_html):
    from app.models import SiteCrawl

    return SiteCrawl(site="acme.test", start_url="https://acme.test/",
                     pages=[extract(RawPage(url=u, status=200, html=h)) for u, h in pages_html])


def test_ingest_is_incremental(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    llm = FakeLLM()
    _, s1 = pipeline.ingest(_crawl(("https://acme.test/", HOME), ("https://acme.test/services", SERVICES)), llm)
    assert s1["added"] == 2
    # second run: home unchanged, services changed, contact new
    _, s2 = pipeline.ingest(
        _crawl(("https://acme.test/", HOME), ("https://acme.test/services", SERVICES.replace("SEO", "Ads")),
               ("https://acme.test/contact", CONTACT.replace("</body>", "<p>" + "more words here " * 10 + "</p></body>"))),
        llm,
    )
    assert s2 == {"added": 1, "updated": 1, "unchanged": 1, "removed": 0, "skipped_empty": 0}
    # third run: services page disappeared from the site
    _, s3 = pipeline.ingest(_crawl(("https://acme.test/", HOME)), llm)
    assert s3["removed"] == 2 and len(llm.docs) == 1


# ------------------------------------------------------------------ Claude analyzer


def test_strict_schema_is_self_contained():
    s = json.dumps(strict_schema(ReportDraft))
    assert "$ref" not in s and "$defs" not in s
    schema = strict_schema(ReportDraft)
    assert schema["additionalProperties"] is False and set(schema["required"]) == set(schema["properties"])


class FakeAnthropic:
    def __init__(self, payloads):
        self.payloads, self.calls = list(payloads), []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.payloads.pop(0))])


DRAFT = {
    "summary": "Clear offer for clinics.",
    "audience_guess": "Private clinic owners",
    "sections": [{"name": "Positioning & offer", "score": 14, "findings": [{"text": "Strong H1", "evidence_url": "https://acme.test/"}],
                  "recommendations": ["Add pricing"]}],
    "top_priorities": [{"title": "Add testimonials", "why": "No proof", "impact": "high", "effort": "low"}],
}


def test_analyzer_report_and_ask():
    llm = FakeLLM()
    llm.add_text_document("s", to_markdown(extract(RawPage(url="https://acme.test/", status=200, html=HOME))), "Acme", "https://acme.test/")
    fake = FakeAnthropic([json.dumps(DRAFT), "They build clinic websites [1]."])
    a = Analyzer(llm, client=fake, model="claude-test")
    report = a.analyze(_crawl(("https://acme.test/", HOME)), "s")
    assert report.sections[0].score == 10  # clamped
    assert report.top_priorities[0].impact == "high" and report.model == "claude-test"
    assert report.chunks_retrieved == 1  # deduped across all queries
    req = fake.calls[0]
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert "Extracted facts" in req["messages"][0]["content"] and "https://acme.test/" in req["messages"][0]["content"]

    ans = a.ask("s", "What do they do?")
    assert "[1]" in ans.answer and ans.sources[0]["url"] == "https://acme.test/"


def test_analyzer_rejects_bad_output():
    llm = FakeLLM()
    with pytest.raises(RuntimeError, match="schema"):
        Analyzer(llm, client=FakeAnthropic(['{"summary": 1}']), model="m").analyze(_crawl(("https://acme.test/", HOME)), "s")


# ------------------------------------------------------------------ end to end + API


@respx.mock
def test_end_to_end_and_api(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "request_delay_s", 0)
    monkeypatch.setattr(settings, "render_js", "never")
    _mock_site(respx.mock)
    llm = FakeLLM()
    monkeypatch.setattr(pipeline, "AnythingLLM", lambda: llm)
    monkeypatch.setattr("app.analyzer.anthropic.Anthropic", lambda **kw: FakeAnthropic([json.dumps(DRAFT)]))

    report = asyncio.run(pipeline.run_site("https://acme.test/", 10))
    html = (tmp_path / "acme.test" / "report.html").read_text("utf-8")
    assert report.pages_analyzed == 3 and "Add testimonials" in html and "Page inventory" in html

    from fastapi.testclient import TestClient
    from app.api import app

    client = TestClient(app)
    assert client.get("/sites").json()[0]["site"] == "acme.test"
    assert client.get("/sites/acme.test/report").json()["summary"] == DRAFT["summary"]
    assert "Acme" in client.get("/sites/acme.test/report.html").text
    assert client.get("/sites/nope/report").status_code == 404
    assert client.get("/").status_code == 200
