#!/usr/bin/env python3
"""
Load bundled seed pages so the assistant has data without Apify.

Usage:
  python scripts/seed_knowledge.py              # copy seed → pages_latest + update MD
  python scripts/seed_knowledge.py --ingest     # also embed into Chroma (needs OPENAI_API_KEY)
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "data" / "formatted" / "pages_seed.json"
LATEST = ROOT / "data" / "formatted" / "pages_latest.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Install bundled MobCoder knowledge")
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="Run ingest.py to embed into Chroma (requires OPENAI_API_KEY)",
    )
    args = parser.parse_args()

    if not SEED.exists():
        print(f"Missing {SEED}")
        sys.exit(1)

    LATEST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(SEED, LATEST)
    pages = json.loads(SEED.read_text(encoding="utf-8"))
    print(f"Installed {len(pages)} seed pages → {LATEST}")

    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_knowledge_md.py"), "--input", str(LATEST)],
        check=True,
        cwd=str(ROOT),
    )

    if args.ingest:
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "ingest.py"),
                "--input",
                str(LATEST),
                "--reset-collection",
            ],
            check=True,
            cwd=str(ROOT),
        )
        print("Chroma ingest complete.")
    else:
        print(
            "\nSeed pages are active via keyword fallback (no OpenAI needed for retrieval).\n"
            "For best quality, set OPENAI_API_KEY and run:\n"
            "  python scripts/seed_knowledge.py --ingest\n"
            "Or crawl live site:\n"
            "  python scripts/crawl_mobcoder.py && python scripts/ingest.py\n"
        )


if __name__ == "__main__":
    main()
