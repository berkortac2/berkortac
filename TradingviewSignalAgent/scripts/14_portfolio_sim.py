"""What the bot would have done with the locked-test trades of the 5m model:
N concurrent positions, budget B (margin), leverage L, one position per coin, no compounding.
Uses reports/deep5m_final_trades.parquet (research trades, taker fees + slippage included)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REP = ROOT / "reports"
BAR = 300_000


def sim(tr: pd.DataFrame, H: int, n_pos: int, lev: float, budget: float = 1000.0):
    tr = tr.sort_values("time")
    busy_until = []            # exit times of open positions
    open_coin = {}
    eq, rows = 0.0, []
    for r in tr.itertuples():
        entry = r.time + BAR
        busy_until = [x for x in busy_until if x > entry]
        open_coin = {c: x for c, x in open_coin.items() if x > entry}
        if len(busy_until) >= n_pos or r.coin in open_coin:
            continue
        ex = r.time + (H + 1) * BAR
        busy_until.append(ex)
        open_coin[r.coin] = ex
        pnl = budget / n_pos * lev * r.net
        eq += pnl
        rows.append((ex, pnl))
    if not rows:
        return {"trades": 0}
    s = pd.DataFrame(rows, columns=["t", "pnl"]).sort_values("t")
    curve = budget + s.pnl.cumsum()
    dd = float(((curve.cummax() - curve) / curve.cummax()).max())
    s["m"] = pd.to_datetime(s.t, unit="ms").dt.to_period("M")
    monthly = (s.groupby("m").pnl.sum() / budget * 100).round(2)
    return {"trades": len(s), "total_pct": round(eq / budget * 100, 2), "max_dd_pct": round(dd * 100, 2),
            "months_pos": int((monthly > 0).sum()), "months": len(monthly),
            "monthly_pct": {str(k): float(v) for k, v in monthly.items()}}


def main():
    t = pd.read_parquet(REP / "bot_trades_5m.parquet")
    H = json.loads((ROOT / "bot" / "model" / "model_5m.json").read_text())["long"]["H"]
    out = {}
    for model in ("v1", "v1_stop8"):
        for period, sel in (("locked_2026-02_09_36coins", t.group == "locked36"),
                            ("holdout10_2025-01_2026-02", t.group == "holdout10_pre")):
            tr = t[sel & (t.model == model)]
            for n in (2, 4, 8):
                for lev in (1, 2):
                    r = sim(tr, H, n, lev)
                    out[f"{model}|{period}|N={n}|L={lev}"] = r
                    print(model, period, n, lev, {k: v for k, v in r.items() if k != "monthly_pct"})
    (REP / "portfolio_sim_5m.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
