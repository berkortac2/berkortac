"""Account-level returns of the live 5m bot (AL rule v1 + ÇIK exit policy), net of taker fees, slippage
AND the real Binance funding payments of every position.

Trades come from tsa.exits.sim_trades (same logic as the bot). Funding: realised rates from
data.binance.vision (a long pays rate x notional at every funding time it is open across; months not yet
published are filled with the coin's last known interval and its median rate of the previous 30 days).

Portfolio: budget B, N slots, leverage L, one position per coin, margin per trade = min(B, equity)/N
(bot default, profits not reused) or equity/N (compounding). Periods:
  early  : 12 perps 2020-01..2023-08            (never seen by the AL rule)
  mid    : 12 perps 2023-09..2026-02-16         (the AL rule was fitted here -> in-sample)
  hold25 : 10 never-used coins 2025-01..2026-02-16
  locked : 36 coins 2026-02-16..2026-09-28
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tsa.data.binance_um import download_funding  # noqa: E402
from tsa.dataset import final_cutoff, load_dataset, load_universe  # noqa: E402
from tsa.exits import EXIT_FEATS, sim_trades  # noqa: E402
from tsa.models import offline as off  # noqa: E402
from tsa.search.deep5m import FEATURE_DIR  # noqa: E402

BAR = 300_000
DAY = 86_400_000
COST = 0.0014
SPLIT = 1693526400000  # 2023-09-01
FUND_DIR = ROOT / "data" / "funding"
POLICIES = {  # name: (Hmax, tp, sl, emergency, ind feature, thr)
    "new": (96, 0.0, 3.0, 0.08, "wt", 1.0),     # ÇIK: WaveTrend >= 50, 3xATR / 8% stop, max 96 bars
    "old": (24, 0.0, 0.0, 0.08, None, 0.0),     # previous bot: 24 bars + 8% emergency stop
}
GRID_N = (1, 2, 3, 4, 6, 8)
GRID_L = (1, 2, 3)


def months_between(t0: int, t1: int) -> list[str]:
    a = dt.datetime.utcfromtimestamp(t0 / 1000).date().replace(day=1)
    b = dt.datetime.utcfromtimestamp(t1 / 1000).date()
    out = []
    while a <= b:
        out.append(f"{a.year}-{a.month:02d}")
        a = (a.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


def funding_for(sym: str, t0: int, t1: int) -> tuple[pd.DataFrame, bool]:
    """Funding events of `sym` covering [t0, t1]; second value = True if some were extrapolated."""
    FUND_DIR.mkdir(parents=True, exist_ok=True)
    p = FUND_DIR / f"{sym}.parquet"
    have = pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=["calc_time", "interval_h", "rate"])
    need = months_between(t0, t1)
    got = set(pd.to_datetime(have.calc_time, unit="ms").dt.strftime("%Y-%m")) if len(have) else set()
    missing = [m for m in need if m not in got]
    if missing:
        new = download_funding(sym, missing)
        if len(new):
            have = pd.concat([have, new]).drop_duplicates("calc_time").sort_values("calc_time")
            have = have.astype({"calc_time": "int64", "interval_h": "int64", "rate": "float64"})
            have.to_parquet(p, index=False)
    f = have[(have.calc_time >= t0 - DAY) & (have.calc_time <= t1 + DAY)].copy()
    extrap = False
    last = int(have.calc_time.max()) if len(have) else 0
    if last < t1:
        extrap = True
        ih = int(have.interval_h.iloc[-1]) if len(have) else 8
        recent = have[have.calc_time >= last - 30 * DAY].rate if len(have) else pd.Series([0.0001])
        step = ih * 3_600_000
        start = (last // step + 1) * step if last else (t0 // step) * step
        ts = np.arange(start, t1 + step, step, dtype=np.int64)
        f = pd.concat([f, pd.DataFrame({"calc_time": ts, "interval_h": ih, "rate": float(recent.median())})])
    return f.sort_values("calc_time").reset_index(drop=True), extrap


def load(period, uni, cut, feats):
    kw = dict(features=feats, feature_dir=FEATURE_DIR)
    if period == "early":
        return load_dataset("5m", uni["search5m"], t_max=SPLIT, **kw)
    if period == "mid":
        return load_dataset("5m", uni["search5m"], t_min=SPLIT, t_max=cut, **kw)
    if period == "hold25":
        return load_dataset("5m", uni["holdout2"], t_max=cut, **kw)
    return load_dataset("5m", uni["train"] + uni["unseen"] + uni["holdout2"], t_min=cut, **kw)


def trades(ds, rules, policy) -> pd.DataFrame:
    fi = {n: i for i, n in enumerate(ds.feat_names)}
    sig = np.zeros(ds.n, bool)
    for r in rules:
        sig |= off.rule_mask(ds.X, [(fi[f], op, v) for f, op, v in r])
    F = np.ascontiguousarray(ds.X[:, [fi[f] for f in EXIT_FEATS]].astype(np.float64))
    Hm, tp, sl, em, ind, thr = policy
    iid = EXIT_FEATS.index(ind) if ind else -1
    t, x, net, bars, why = sim_trades(ds.o, ds.h, ds.l, ds.c, ds.atr, sig, F, ds.seg_start, ds.seg_end,
                                      Hm, tp, sl, em, 0.0, 0.0, iid, thr, COST)
    entry = ds.time[t] + BAR + 3_000                                        # market order 3 s after close
    exit_ = np.where(why == 1, ds.time[x] + BAR // 2, ds.time[x] + BAR + 3_000)  # stop: inside the bar
    return pd.DataFrame({"coin": [ds.symbols[i] for i in ds.coin[t]], "entry": entry, "exit": exit_,
                         "net_fee": net, "why": why, "bars": bars})


def add_funding(tr: pd.DataFrame) -> tuple[pd.DataFrame, list]:
    fund = np.zeros(len(tr))
    extrap = []
    for coin, g in tr.groupby("coin"):
        f, ex = funding_for(coin, int(g.entry.min()), int(g.exit.max()))
        if ex:
            extrap.append(coin)
        ft, cr = f.calc_time.to_numpy(), np.concatenate([[0.0], np.cumsum(f.rate.to_numpy())])
        a = np.searchsorted(ft, g.entry.to_numpy(), side="right")   # first funding strictly after entry
        b = np.searchsorted(ft, g.exit.to_numpy(), side="right")    # funding at or before exit
        fund[g.index.to_numpy()] = cr[b] - cr[a]                     # long pays positive rates
    tr = tr.copy()
    tr["funding"] = fund
    tr["net"] = tr.net_fee - fund
    return tr, extrap


def portfolio(tr: pd.DataFrame, n_pos: int, lev: float, compound: bool, budget: float = 1000.0) -> dict:
    tr = tr.sort_values("entry")
    eq = budget
    open_ = []                      # (exit, coin, pnl)
    done = []                       # (exit, pnl, notional)
    for r in tr.itertuples():
        keep = []
        for o in open_:
            if o[0] <= r.entry:
                eq += o[2]
                done.append((o[0], o[2], o[3]))
            else:
                keep.append(o)
        open_ = keep
        if len(open_) >= n_pos or any(o[1] == r.coin for o in open_):
            continue
        margin = (eq if compound else min(budget, eq)) / n_pos
        if margin <= 0:
            continue
        notional = margin * lev
        open_.append((r.exit, r.coin, notional * r.net, notional))
    for o in open_:
        eq += o[2]
        done.append((o[0], o[2], o[3]))
    if not done:
        return {"trades": 0}
    s = pd.DataFrame(done, columns=["t", "pnl", "notional"]).sort_values("t")
    curve = budget + s.pnl.cumsum()
    peak = np.maximum.accumulate(np.concatenate([[budget], curve.to_numpy()]))[1:]
    dd = float(((peak - curve) / peak).max())
    d = pd.to_datetime(s.t, unit="ms")
    daily = s.groupby(d.dt.date).pnl.sum() / budget * 100
    monthly = s.groupby(d.dt.to_period("M")).pnl.sum() / budget * 100
    yearly = s.groupby(d.dt.year).pnl.sum() / budget * 100
    return {"trades": len(s), "total_pct": round((curve.iloc[-1] / budget - 1) * 100, 2),
            "max_dd_pct": round(dd * 100, 2), "best_day_pct": round(float(daily.max()), 2),
            "worst_day_pct": round(float(daily.min()), 2), "months_pos": int((monthly > 0).sum()),
            "months": len(monthly), "monthly_pct": {str(k): round(float(v), 2) for k, v in monthly.items()},
            "yearly_pct": {str(k): round(float(v), 2) for k, v in yearly.items()}}


def exposure(tr: pd.DataFrame, t0: int, t1: int) -> dict:
    """How much of the time the strategy is in the market at all (all signals, no slot limit)."""
    ev = np.concatenate([np.c_[tr.entry, np.ones(len(tr))], np.c_[tr.exit, -np.ones(len(tr))]])
    ev = ev[np.argsort(ev[:, 0], kind="stable")]
    cnt = np.cumsum(ev[:, 1])
    dt_ = np.diff(np.append(ev[:, 0], ev[-1, 0]))
    span = t1 - t0
    return {"avg_open_positions": round(float((cnt * dt_).sum() / span), 3),
            "time_in_market_pct": round(float(dt_[cnt > 0].sum() / span * 100), 1),
            "max_open_positions": int(cnt.max())}


def buy_hold(ds) -> dict:
    rets = {}
    for s, e, ci in zip(ds.seg_start, ds.seg_end, ds.coin[ds.seg_start]):
        rets[ds.symbols[ci]] = float(ds.c[e - 1] / ds.o[s] - 1)
    return {"BTCUSDT_pct": round(rets.get("BTCUSDT", np.nan) * 100, 1),
            "equal_weight_pct": round(float(np.mean(list(rets.values()))) * 100, 1)}


def main():
    uni = load_universe()
    cut = final_cutoff("5m")
    rules = json.loads((ROOT / "bot" / "model" / "model_5m.json").read_text())["long"]["rules"]
    feats = sorted({f for r in rules for f, _, _ in r} | set(EXIT_FEATS))
    out = {}
    all_tr = []
    for period in ("locked", "hold25", "mid", "early"):
        ds = load(period, uni, cut, feats)
        t0, t1 = int(ds.time.min()), int(ds.time.max()) + BAR
        days = (t1 - t0) / DAY
        res = {"from": str(pd.to_datetime(t0, unit="ms").date()), "to": str(pd.to_datetime(t1, unit="ms").date()),
               "days": round(days, 1), "coins": len(ds.symbols), "buy_hold": buy_hold(ds)}
        for pname, pol in POLICIES.items():
            tr = trades(ds, rules, pol)
            tr, extrap = add_funding(tr)
            tr["period"], tr["policy"] = period, pname
            all_tr.append(tr)
            r = {"trades": len(tr), "avg_net_fee_pct": round(tr.net_fee.mean() * 100, 3),
                 "avg_funding_pct": round(tr.funding.mean() * 100, 4),
                 "avg_net_pct": round(tr.net.mean() * 100, 3), "win_pct": round((tr.net > 0).mean() * 100, 1),
                 "sum_net_pct": round(tr.net.sum() * 100, 1), "best_trade_pct": round(tr.net.max() * 100, 2),
                 "worst_trade_pct": round(tr.net.min() * 100, 2), "avg_hold_min": round(tr.bars.mean() * 5, 0),
                 "funding_extrapolated": extrap, "exposure": exposure(tr, t0, t1), "portfolio": {}}
            for n in GRID_N:
                for lev in GRID_L:
                    for comp in (False, True):
                        r["portfolio"][f"N={n}|L={lev}|{'compound' if comp else 'fixed'}"] = portfolio(tr, n, lev, comp)
            out.setdefault(period, res)[pname] = r
            p8 = r["portfolio"]["N=8|L=1|fixed"]
            p2 = r["portfolio"]["N=2|L=1|fixed"]
            print(period, pname, {k: r[k] for k in ("trades", "avg_net_fee_pct", "avg_funding_pct", "avg_net_pct",
                                                     "win_pct", "sum_net_pct")}, r["exposure"],
                  "N8L1", p8["total_pct"], p8["max_dd_pct"], "N2L1", p2["total_pct"], p2["max_dd_pct"], flush=True)
        print(period, "buy&hold", res["buy_hold"], flush=True)
        del ds
    (ROOT / "reports" / "account_returns_5m.json").write_text(json.dumps(out, indent=1))
    pd.concat(all_tr).to_parquet(ROOT / "reports" / "account_trades_5m.parquet", index=False)


if __name__ == "__main__":
    main()
