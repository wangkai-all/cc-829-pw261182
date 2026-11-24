#!/usr/bin/env python3
"""Run the independent small-scale enumeration check against the seed data."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.engine import enumeration_check  # noqa: E402


def main() -> int:
    seed_path = ROOT / "app" / "data" / "seed.json"
    dataset = json.loads(seed_path.read_text(encoding="utf-8"))
    report = enumeration_check(dataset, grid_n=220)
    printable = {k: v for k, v in report.items() if k != "sample_grid_witnesses"}
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    if not report["sound"]:
        print("ENUMERATION FAILED: grid winner outside exact set", file=sys.stderr)
        return 1
    if not report["grid_covers_exact_set"]:
        print("WARNING: grid missed a narrow exact witness; soundness still holds", file=sys.stderr)
    print(f"OK: {report['score_evaluations']} score evaluations; exact set {report['exact_winner_ids']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
