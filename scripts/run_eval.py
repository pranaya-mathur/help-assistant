#!/usr/bin/env python3
"""Thin wrapper for the canonical root eval runner."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT_RUNNER = Path(__file__).resolve().parents[1] / "run_eval.py"
ROOT_DIR = ROOT_RUNNER.parent


def _load_root_runner():
    sys.path.insert(0, str(ROOT_DIR))
    spec = importlib.util.spec_from_file_location("mobcoder_root_run_eval", ROOT_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load eval runner at {ROOT_RUNNER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    _load_root_runner().main()
