"""Renders a SiteReport into a standalone, printable HTML page."""
from __future__ import annotations

from jinja2 import Environment, select_autoescape

from .models import SiteCrawl, SiteReport

_env = Environment(autoescape=select_autoescape(default=True), trim_blocks=True, lstrip_blocks=True)

TEMPLATE = _env.from_string(
    """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Site audit · {{ r.site }}</title>
<style>
:root{--bg:#fafaf7;--fg:#1c1c1a;--muted:#6b6b66;--line:#e4e3dc;--card:#fff;--accent:#2f5d50;--hi:#b4462f;--mid:#b8862b;--lo:#2f7d4f}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ecebe6;--muted:#9c9b94;--line:#2c2c29;--card:#1c1c1a;--accent:#7fb8a4}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:920px;margin:0 auto;padding:40px 16px 80px}
h1{font-size:28px;margin:0 0 4px;letter-spacing:-.01em}h2{font-size:19px;margin:40px 0 12px}h3{font-size:16px;margin:0}
.muted{color:var(--muted);font-size:13px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin:12px 0}
.scores{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.score{display:flex;justify-content:space-between;align-items:center;gap:8px}.bar{height:6px;background:var(--line);border-radius:3px;margin-top:6px}
.bar i{display:block;height:100%;border-radius:3px;background:var(--accent)}
.tag{display:inline-block;font-size:11px;padding:1px 8px;border-radius:99px;border:1px solid var(--line);margin-left:6px}
.high{color:var(--hi)}.medium{color:var(--mid)}.low{color:var(--lo)}
ul{padding-left:20px;margin:8px 0}li{margin:4px 0}a{color:var(--accent)}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
.ev{font-size:12px;color:var(--muted);word-break:break-all}
@media print{body{background:#fff}.card{break-inside:avoid}}
</style></head><body><main>
<p class="muted">Website intelligence report</p>
<h1>{{ r.site }}</h1>
<p class="muted">{{ r.pages_analyzed }} pages crawled · {{ r.chunks_retrieved }} evidence chunks retrieved from AnythingLLM · analysis by {{ r.model }} · {{ r.generated_at.strftime('%Y-%m-%d %H:%M UTC') }}</p>

<div class="card"><p style="margin-top:0">{{ r.summary }}</p><p class="muted" style="margin-bottom:0"><b>Likely audience:</b> {{ r.audience_guess }}</p></div>

<h2>Scores</h2>
<div class="scores">{% for s in r.sections %}
<div class="card" style="margin:0"><div class="score"><span>{{ s.name }}</span><b>{{ s.score }}/10</b></div><div class="bar"><i style="width:{{ s.score*10 }}%"></i></div></div>
{% endfor %}</div>

<h2>Top priorities</h2>
{% for p in r.top_priorities %}
<div class="card"><h3>{{ loop.index }}. {{ p.title }}<span class="tag {{ p.impact }}">impact: {{ p.impact }}</span><span class="tag">effort: {{ p.effort }}</span></h3><p style="margin:8px 0 0">{{ p.why }}</p></div>
{% endfor %}

{% for s in r.sections %}
<h2>{{ s.name }} <span class="muted">· {{ s.score }}/10</span></h2>
<div class="card"><b>Findings</b><ul>{% for f in s.findings %}<li>{{ f.text }}{% if f.evidence_url %}<div class="ev">↳ <a href="{{ f.evidence_url }}">{{ f.evidence_url }}</a></div>{% endif %}</li>{% endfor %}</ul>
<b>Recommendations</b><ul>{% for rec in s.recommendations %}<li>{{ rec }}</li>{% endfor %}</ul></div>
{% endfor %}

<h2>Page inventory</h2>
<div class="card" style="overflow-x:auto"><table><tr><th>Page</th><th>Words</th><th>Rule-based issues</th></tr>
{% for p in c.pages %}<tr><td><a href="{{ p.url }}">{{ p.title or p.url }}</a><div class="ev">{{ p.url }}</div></td><td>{{ p.word_count }}</td><td>{{ p.issues | join('; ') or '—' }}</td></tr>{% endfor %}
</table></div>
</main></body></html>"""
)


def render_html(report: SiteReport, crawl: SiteCrawl) -> str:
    return TEMPLATE.render(r=report, c=crawl)
