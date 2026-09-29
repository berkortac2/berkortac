"""Extend the 5m research data further back in time and add fresh hold-out coins.

* perp 5m back to 2020-01 for the 5m search coins (Binance USDT-M data starts 2020-01)
* spot 5m + 1h for 2017-08 .. 2019-12 (pre-futures history incl. the 2018 bear market);
  stored as separate segments "<SYM>@spot" so the replay never stitches venues together
* perp 5m + 1h for 10 liquid perps never used anywhere before ("holdout-2")
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tsa.data.binance_um import backfill, download_klines, download_range, quality_report  # noqa: E402

RAW = ROOT / "data" / "raw"
SEARCH5 = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT", "NEARUSDT", "UNIUSDT",
           "ADAUSDT", "LINKUSDT", "AVAXUSDT", "BCHUSDT"]
SPOT = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "LINKUSDT", "LTCUSDT", "DOGEUSDT"]
HOLDOUT2 = ["ONDOUSDT", "INJUSDT", "FETUSDT", "APTUSDT", "ETCUSDT", "OPUSDT", "CRVUSDT", "TIAUSDT",
            "ICPUSDT", "WIFUSDT"]


def main():
    for s in SEARCH5:
        old = download_range(s, "5m", "2020-01-01", "2023-09-30")
        df = backfill(RAW / f"{s}_5m.parquet", old)
        print("perp", s, quality_report(df, "5m"), flush=True)
    for s in SPOT:
        for tf in ("5m", "1h"):
            df = download_range(s, tf, "2017-08-01", "2019-12-31", market="spot")
            if len(df):
                df.to_parquet(RAW / f"{s}@spot_{tf}.parquet", index=False)
                print("spot", s, tf, quality_report(df, tf), flush=True)
    for s in HOLDOUT2:
        for tf in ("5m", "1h"):
            df = download_klines(s, tf, "2025-01-01", RAW)
            print("holdout2", s, tf, quality_report(df, tf), flush=True)
    uni = json.loads((ROOT / "config" / "universe.json").read_text())
    uni["holdout2"] = HOLDOUT2
    uni["search5m"] = SEARCH5
    uni["spot5m"] = [f"{s}@spot" for s in SPOT]
    (ROOT / "config" / "universe.json").write_text(json.dumps(uni, indent=2))


if __name__ == "__main__":
    main()
