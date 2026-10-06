"""Command line entry point.

  python -m app.cli run                      # every site in sites.yaml
  python -m app.cli run https://vdigital.dev # one site
  python -m app.cli ask vdigital.dev "What services are offered and how do I contact them?"
  python -m app.cli crawl https://vdigital.dev  # crawl + extract only (no API keys needed)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import yaml

from .analyzer import Analyzer
from .anythingllm import AnythingLLM
from .crawler import Crawler
from .pipeline import run_site, site_dir, workspace_name


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(prog="site-intel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="full pipeline")
    r.add_argument("urls", nargs="*")
    r.add_argument("--config", default="sites.yaml")
    r.add_argument("--max-pages", type=int)
    c = sub.add_parser("crawl", help="crawl + extract only, writes data/<site>/crawl.json")
    c.add_argument("url")
    c.add_argument("--max-pages", type=int)
    q = sub.add_parser("ask", help="RAG question against an ingested site")
    q.add_argument("site")
    q.add_argument("question")
    args = ap.parse_args()

    if args.cmd == "crawl":
        crawl = asyncio.run(Crawler(args.url, args.max_pages).run())
        out = site_dir(crawl.site) / "crawl.json"
        out.write_text(crawl.model_dump_json(indent=2), "utf-8")
        for p in crawl.pages:
            print(f"{p.word_count:>5} words  {p.rendered_with:<7}  {p.url}  issues={len(p.issues)}")
        print(f"\nSaved {len(crawl.pages)} pages to {out}")

    elif args.cmd == "run":
        urls = args.urls
        if not urls:
            cfg = yaml.safe_load(Path(args.config).read_text("utf-8")) or {}
            urls = [s["url"] for s in cfg.get("sites", []) if s.get("enabled", True)]
        failed = 0
        for url in urls:
            try:
                rep = asyncio.run(run_site(url, args.max_pages))
                avg = sum(s.score for s in rep.sections) / max(1, len(rep.sections))
                print(f"✔ {rep.site}: {rep.pages_analyzed} pages, avg score {avg:.1f}/10 → data/{rep.site}/report.html")
            except Exception as e:
                failed += 1
                logging.exception("✘ %s failed: %s", url, e)
        sys.exit(1 if failed else 0)

    elif args.cmd == "ask":
        llm = AnythingLLM()
        ans = Analyzer(llm).ask(llm.get_or_create_workspace(workspace_name(args.site)), args.question)
        print(ans.answer, "\n")
        for s in ans.sources:
            print(f"[{s['n']}] {s['url']}  (score {s['score']:.2f})" if s.get("score") is not None else f"[{s['n']}] {s['url']}")


if __name__ == "__main__":
    main()
