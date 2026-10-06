"""HTML -> structured PageData. Pure functions, no network: easy to unit-test."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urldefrag, urljoin, urlparse, urlencode, parse_qsl

from bs4 import BeautifulSoup, Tag

from .models import PageData, RawPage

SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|webp|svg|ico|mp4|webm|mov|zip|rar|docx?|xlsx?|pptx?|css|js|json|xml|txt)$", re.I)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+\d{1,3}[\s\-]?\(?\d{1,4}\)?(?:[\s\-]?\d{2,4}){2,4}")
CTA_WORDS = re.compile(
    r"\b(contact|get started|book|schedule|start|try|sign ?up|buy|order|request|quote|call|demo|subscribe|apply|join"
    r"|kontakt|zakaži|zakazi|pošalji|posalji|prijavi|poruči|poruci|pozovi|započni|zapocni"
    r"|связаться|записаться|заказать|начать|оставить заявку|написать|купить|получить)\b",
    re.I,
)
NOISE_TAGS = ["script", "style", "noscript", "svg", "iframe", "template", "nav", "footer"]


# ---------------------------------------------------------------- URL helpers


def site_key(url: str) -> str:
    """'https://www.VDigital.dev/x' -> 'vdigital.dev'"""
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def normalize_url(href: str, base: str) -> str | None:
    """Absolute, fragment-free, tracking-free URL — or None if it's not a crawlable page link."""
    if not href:
        return None
    href = href.strip()
    if href.startswith(("mailto:", "tel:", "javascript:", "data:", "#")):
        return None
    url, _ = urldefrag(urljoin(base, href))
    p = urlparse(url)
    if p.scheme not in ("http", "https") or SKIP_EXT.search(p.path):
        return None
    query = urlencode([(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith(("utm_", "fbclid", "gclid"))])
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return p._replace(netloc=p.netloc.lower(), path=path, query=query).geturl()


def is_same_site(url: str, root: str) -> bool:
    return site_key(url) == site_key(root)


# ---------------------------------------------------------------- extraction


def _texts(soup: BeautifulSoup, tag: str, limit: int = 30) -> list[str]:
    out = []
    for el in soup.find_all(tag)[:limit]:
        t = " ".join(el.get_text(" ", strip=True).split())
        if t:
            out.append(t)
    return out


def _main_text(soup: BeautifulSoup) -> str:
    """Readable text of the page body with markdown-style headings; nav/footer/scripts removed."""
    doc = BeautifulSoup(str(soup), "lxml")  # work on a copy
    for t in doc.find_all(NOISE_TAGS):
        t.decompose()
    root = doc.find("main") or doc.body or doc
    for level in range(1, 7):
        for h in root.find_all(f"h{level}"):
            h.string = "#" * level + " " + " ".join(h.get_text(" ", strip=True).split())
    lines, prev = [], None
    for raw in root.get_text("\n").splitlines():
        line = " ".join(raw.split())
        if line and line != prev and not re.fullmatch(r"#+", line):
            lines.append(line)
            prev = line
    return "\n".join(lines)


def _json_ld_types(soup: BeautifulSoup) -> list[str]:
    types: list[str] = []
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for it in items:
            if isinstance(it, dict) and it.get("@type"):
                t = it["@type"]
                types.extend(t if isinstance(t, list) else [t])
    return sorted(set(map(str, types)))


def _ctas(soup: BeautifulSoup) -> list[str]:
    found: list[str] = []
    for el in soup.find_all(["a", "button"]):
        if not isinstance(el, Tag):
            continue
        text = " ".join(el.get_text(" ", strip=True).split())
        if not text or len(text) > 60:
            continue
        cls = " ".join(el.get("class", [])).lower()
        if el.name == "button" or any(k in cls for k in ("btn", "button", "cta")) or CTA_WORDS.search(text):
            if text not in found:
                found.append(text)
    return found[:25]


def _issues(p: PageData) -> list[str]:
    issues = []
    if p.status >= 400:
        issues.append(f"HTTP {p.status}")
    if not p.title:
        issues.append("Missing <title>")
    elif not 15 <= len(p.title) <= 65:
        issues.append(f"Title length {len(p.title)} chars (aim 15–65)")
    if not p.meta_description:
        issues.append("Missing meta description")
    elif not 50 <= len(p.meta_description) <= 165:
        issues.append(f"Meta description {len(p.meta_description)} chars (aim 50–165)")
    if not p.h1:
        issues.append("No H1")
    elif len(p.h1) > 1:
        issues.append(f"{len(p.h1)} H1 tags (use one)")
    if not p.canonical:
        issues.append("No canonical URL")
    if not p.lang:
        issues.append("No <html lang> attribute")
    if p.word_count < 150:
        issues.append(f"Thin content ({p.word_count} words)")
    if p.images_missing_alt:
        issues.append(f"{p.images_missing_alt}/{p.images_total} images missing alt text")
    if not p.has_json_ld:
        issues.append("No JSON-LD structured data")
    if not p.ctas:
        issues.append("No clear call-to-action found")
    if not p.og.get("og:image"):
        issues.append("No og:image (poor link previews when shared)")
    return issues


def extract(raw: RawPage) -> PageData:
    soup = BeautifulSoup(raw.html, "lxml")
    base = raw.url

    def meta(name: str, attr: str = "name") -> str:
        el = soup.find("meta", attrs={attr: name})
        return (el.get("content") or "").strip() if el else ""

    canonical_el = soup.find("link", rel=lambda v: v and "canonical" in v)
    html_el = soup.find("html")

    internal, external = [], []
    for a in soup.find_all("a", href=True):
        url = normalize_url(a["href"], base)
        if not url:
            continue
        bucket = internal if is_same_site(url, base) else external
        if url not in bucket:
            bucket.append(url)

    hrefs = [a.get("href", "") for a in soup.find_all("a", href=True)]
    emails = {h[7:].split("?")[0] for h in hrefs if h.lower().startswith("mailto:")}
    phones = {h[4:].strip() for h in hrefs if h.lower().startswith("tel:")}

    text = _main_text(soup)
    emails |= set(EMAIL_RE.findall(text))
    phones |= {" ".join(m.split()) for m in PHONE_RE.findall(text)}

    imgs = soup.find_all("img")
    ld_types = _json_ld_types(soup)

    page = PageData(
        url=raw.url,
        status=raw.status,
        title=" ".join((soup.title.get_text() if soup.title else "").split()),
        meta_description=meta("description"),
        canonical=(canonical_el.get("href") or "").strip() if canonical_el else "",
        lang=(html_el.get("lang") or "").strip() if html_el else "",
        og={k: meta(k, "property") for k in ("og:title", "og:description", "og:image", "og:type") if meta(k, "property")},
        h1=_texts(soup, "h1"),
        h2=_texts(soup, "h2"),
        h3=_texts(soup, "h3"),
        text=text,
        word_count=len(re.findall(r"\w+", text)),
        internal_links=internal,
        external_links=external[:50],
        ctas=_ctas(soup),
        emails=sorted(e for e in emails if not e.lower().endswith((".png", ".jpg", ".webp"))),
        phones=sorted(phones),
        images_total=len(imgs),
        images_missing_alt=sum(1 for i in imgs if not (i.get("alt") or "").strip()),
        has_json_ld=bool(ld_types) or bool(soup.find("script", type="application/ld+json")),
        json_ld_types=ld_types,
        rendered_with=raw.rendered_with,
    )
    page.issues = _issues(page)
    page.content_hash = hashlib.sha256(f"{page.title}\n{page.meta_description}\n{page.text}".encode()).hexdigest()[:16]
    return page


def to_markdown(page: PageData) -> str:
    """The document we embed in AnythingLLM: facts header + clean page text."""
    header = [
        f"# {page.title or page.url}",
        f"URL: {page.url}",
        f"Meta description: {page.meta_description or '(none)'}",
        f"Language: {page.lang or '(not set)'}",
        f"Calls to action: {', '.join(page.ctas) or '(none found)'}",
        f"Contact: {', '.join(page.emails + page.phones) or '(none found)'}",
        f"Structured data: {', '.join(page.json_ld_types) or '(none)'}",
        f"Detected issues: {'; '.join(page.issues) or '(none)'}",
        "",
        "---",
        "",
    ]
    return "\n".join(header) + page.text
