#!/usr/bin/env python3
"""Build docs/mobcoder_knowledge_base.md from crawled pages (optional LLM polish)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config.settings import get_settings  # noqa: E402
from app.crawler.page_loader import _is_error_page  # noqa: E402


def build_markdown(pages: list[dict]) -> str:
    lines = [
        "# MobCoder Knowledge Base",
        "",
        "> Auto-generated from mobcoder.ai crawl. Re-run after site updates.",
        "",
    ]
    by_category: dict[str, list[dict]] = {}
    for p in pages:
        if _is_error_page(p.get("page_title", ""), p.get("clean_text", "")):
            continue
        cat = p.get("page_category", "general")
        by_category.setdefault(cat, []).append(p)

    for cat in sorted(by_category.keys()):
        lines.append(f"## {cat.replace('_', ' ').title()}")
        lines.append("")
        for page in by_category[cat]:
            title = page.get("page_title") or "Untitled"
            url = page.get("source_url", "")
            text = (page.get("clean_text") or "")[:4000]
            lines.append(f"### {title}")
            lines.append(f"**URL:** {url}")
            lines.append("")
            lines.append(text.strip() or "_(no content)_")
            lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default=str(ROOT / "data" / "formatted" / "pages_latest.json"),
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Missing {input_path}. Run crawl first.")
        sys.exit(1)

    with open(input_path, encoding="utf-8") as f:
        pages = json.load(f)

    md = build_markdown(pages)
    settings = get_settings()
    out = Path(settings.knowledge_md_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"Wrote {out} ({len(pages)} pages)")


if __name__ == "__main__":
    main()
