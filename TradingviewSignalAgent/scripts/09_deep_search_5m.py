"""Second-round 5m search on the extended history (see src/tsa/search/deep5m.py).

python scripts/09_deep_search_5m.py [--horizons 6,12,24,48]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tsa.search.deep5m import DeepSearch5m  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizons", default="")
    args = ap.parse_args()
    hs = [int(x) for x in args.horizons.split(",")] if args.horizons else None
    s = DeepSearch5m(ROOT / "reports" / "search", log=lambda m: print(m, flush=True), horizons=hs)
    df = s.run()
    top = df[df.score > -99].sort_values("score", ascending=False)
    cols = ["config", "H", "tp_atr", "sl_atr", "trades", "trades_per_coin_day", "dir_hit", "win_rate",
            "avg_net", "pos_folds", "score"]
    print(top[cols].head(40).to_string())


if __name__ == "__main__":
    main()
