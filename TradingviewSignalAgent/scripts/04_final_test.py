"""Locked final test of the top method families (run once, after the search).

python scripts/04_final_test.py [--top 3] [--tfs 1h,4h]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tsa.final import run_final  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--tfs", default="")
    ap.add_argument("--search", default=str(ROOT / "reports" / "search"))
    ap.add_argument("--out", default=str(ROOT / "reports"))
    args = ap.parse_args()
    tfs = args.tfs.split(",") if args.tfs else None
    res, ranking = run_final(Path(args.search), Path(args.out), args.top, tfs, log=lambda m: print(m, flush=True))
    print(res.to_string())


if __name__ == "__main__":
    main()
