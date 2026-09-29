"""Pick the coin universe by liquidity and download all timeframes.

python scripts/01_download.py [--tfs 1h,4h] [--symbols BTCUSDT,ETHUSDT]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tsa.data.binance_um import download_klines, quality_report, resample_weekly  # noqa: E402


def pick_universe(cfg: dict) -> dict:
    raw = ROOT / cfg["data_dir"]
    rows = []
    for sym in cfg["candidates"]:
        df = download_klines(sym, "1d", "2019-09-01", raw)
        if df.empty:
            continue
        last90 = df.tail(90)
        rows.append({
            "symbol": sym,
            "first": pd.Timestamp(df["open_time"].iloc[0], unit="ms").date().isoformat(),
            "last": pd.Timestamp(df["open_time"].iloc[-1], unit="ms").date().isoformat(),
            "qv90": float(last90["quote_volume"].sum()),
        })
    t = pd.DataFrame(rows).sort_values("qv90", ascending=False)
    newest_last = t["last"].max()
    t = t[t["last"] >= newest_last]  # still trading
    old = t[(t["first"] <= cfg["train_min_history_start"]) & ~t["symbol"].isin(cfg["exclude_from_training"])]
    train = old.head(cfg["n_train"])["symbol"].tolist()
    rest = t[~t["symbol"].isin(train) & (t["first"] <= "2024-09-01")]
    unseen = rest.head(cfg["n_unseen"])["symbol"].tolist()
    t.to_csv(ROOT / "reports" / "universe_liquidity.csv", index=False)
    uni = {"train": train, "unseen": unseen}
    (ROOT / "config" / "universe.json").write_text(json.dumps(uni, indent=2))
    return uni


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tfs", default="")
    ap.add_argument("--symbols", default="")
    args = ap.parse_args()
    cfg = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text())
    uni_path = ROOT / "config" / "universe.json"
    uni = json.loads(uni_path.read_text()) if uni_path.exists() else pick_universe(cfg)
    print("universe", uni, flush=True)
    symbols = args.symbols.split(",") if args.symbols else uni["train"] + uni["unseen"]
    tfs = args.tfs.split(",") if args.tfs else list(cfg["timeframes"])
    q = []
    for tf in tfs:
        for sym in symbols:
            if tf == "1w":  # monthly 1w zips are incomplete -> rebuild weeks from daily klines
                d = download_klines(sym, "1d", cfg["timeframes"]["1d"]["start"], ROOT / cfg["data_dir"])
                df = resample_weekly(d)
                df.to_parquet(ROOT / cfg["data_dir"] / f"{sym}_1w.parquet", index=False)
            else:
                df = download_klines(sym, tf, cfg["timeframes"][tf]["start"], ROOT / cfg["data_dir"])
            if df.empty:
                print(f"{sym} {tf}: no data", flush=True)
                continue
            r = quality_report(df, tf)
            r.update(symbol=sym, tf=tf)
            q.append(r)
            print(r, flush=True)
    if q:
        out = ROOT / "reports" / "data_quality.csv"
        old = pd.read_csv(out) if out.exists() else pd.DataFrame()
        new = pd.concat([old, pd.DataFrame(q)]).drop_duplicates(["symbol", "tf"], keep="last")
        new.to_csv(out, index=False)


if __name__ == "__main__":
    main()
