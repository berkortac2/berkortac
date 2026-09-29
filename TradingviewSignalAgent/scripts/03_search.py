"""Run the strategy search for timeframes.

python scripts/03_search.py --tf 1h [--coins 20] [--horizons 6,12] [--out reports/search]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tsa.search.runner import Search  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", required=True)
    ap.add_argument("--coins", type=int, default=None)
    ap.add_argument("--horizons", default="")
    ap.add_argument("--out", default=str(ROOT / "reports" / "search"))
    args = ap.parse_args()
    hs = [int(x) for x in args.horizons.split(",")] if args.horizons else None
    s = Search(args.tf, Path(args.out), n_coins=args.coins, horizons=hs, log=lambda m: print(m, flush=True))
    df = s.run()
    top = df[df.score > -99].sort_values("score", ascending=False)
    cols = ["family", "config", "H", "tp_atr", "sl_atr", "q", "trades", "dir_hit", "win_rate", "avg_net", "pf", "score"]
    print(top[[c for c in cols if c in top]].head(30).to_string())


if __name__ == "__main__":
    main()
