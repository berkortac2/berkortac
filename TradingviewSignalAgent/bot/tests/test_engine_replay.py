"""Engine on stored candles == research backtest (same entries, same exits, same net)."""
import asyncio

import numpy as np
import pandas as pd
import pytest

import tsabot  # noqa: F401  (path setup)
from tsabot.broker import PaperBroker
from tsabot.config import Settings
from tsabot.engine import Engine
from tsabot.market import ReplayMarket
from tsabot.store import Store
from tsabot.strategy import Strategy
from tsa.features.registry import compute_features
from tsa.indicators import pine_ta as ta
from tsa.labels import simulate, trade_outcomes

SYM = "NEARUSDT"
# a deliberately frequent rule so a short replay produces trades in both directions
MODEL = {"long": {"rules": [[["rsi7", "<=", -0.35]]], "H": 6, "tp_atr": 1.5, "sl_atr": 1.0},
         "short": {"rules": [[["rsi7", ">=", 0.40]]], "H": 6, "tp_atr": 1.5, "sl_atr": 1.0}}
BARS = 700


def run_engine(tmp_path, start):
    s = Settings(mode="replay", symbols=[SYM], budget_usdt=1000, max_positions=1, leverage=1,
                 daily_loss_limit_pct=100, max_drawdown_pct=100, replay_speed=0)
    market = ReplayMarket([SYM], start_ms=start, speed_s=0, bars=BARS)
    eng = Engine(s, Strategy(MODEL), market, PaperBroker(), Store(tmp_path / "t.db"))

    async def go():
        await eng.start()
        await eng.task
    asyncio.run(go())
    return eng


def test_engine_matches_research_backtest(tmp_path):
    raw = tsabot.REPO_ROOT / "data" / "raw"
    if not (raw / f"{SYM}_5m.parquet").exists():
        pytest.skip("research data not downloaded")
    d5 = pd.read_parquet(raw / f"{SYM}_5m.parquet")
    d1 = pd.read_parquet(raw / f"{SYM}_1h.parquet")
    start = int(d5.open_time.iloc[-5000])
    eng = run_engine(tmp_path, start)
    got = pd.DataFrame(eng.store.trades("replay", 10_000)).sort_values("entry_time")
    assert len(got) > 5

    # research: features on full history, rules, triple barrier with SL-first, one position at a time
    f = compute_features(d5, "5m", d1)
    atr = ta.atr(d5.high.to_numpy(), d5.low.to_numpy(), d5.close.to_numpy(), 14)
    t = d5.open_time.to_numpy()
    win = (t >= start) & (t < start + BARS * 300_000)
    L = (f["rsi7"].to_numpy() <= -0.35) & win
    S = (f["rsi7"].to_numpy() >= 0.40) & win & ~L
    o, h, l, c = (d5[k].to_numpy() for k in ("open", "high", "low", "close"))
    nl, el, hl = trade_outcomes(o, h, l, c, atr, 6, 1.5, 1.0, 0.0014, 1)
    ns, es, hs = trade_outcomes(o, h, l, c, atr, 6, 1.5, 1.0, 0.0014, -1)
    rows, dirs, nets, _ = simulate(L, S, nl, el, hl, ns, es, hs, np.array([0]), np.array([len(d5)]))
    ref_rows = [r for r in rows if t[r] + 300_000 <= start + (BARS - 7) * 300_000]
    # engine records entry wall time = close of the signal bar (= open of the next bar)
    eng_sig_bars = set(int(x) - 300_000 for x in got.entry_time)
    ref_sig_bars = set(int(t[r]) for r in ref_rows)
    common = eng_sig_bars & ref_sig_bars
    assert len(common) >= 0.95 * len(ref_sig_bars) and len(common) >= 0.95 * min(len(eng_sig_bars), len(ref_sig_bars))
    # net return per trade (fees + slippage) matches the research cost model
    ref_net = {int(t[r]): n for r, n in zip(rows, nets)}
    got["ret"] = got.pnl / (got.qty * got.entry_price)
    diffs = [abs(g.ret - ref_net[int(g.entry_time) - 300_000]) for g in got.itertuples()
             if int(g.entry_time) - 300_000 in ref_net]
    assert np.median(diffs) < 5e-4


def test_budget_never_exceeded(tmp_path):
    raw = tsabot.REPO_ROOT / "data" / "raw"
    if not (raw / "BTCUSDT_5m.parquet").exists():
        pytest.skip("research data not downloaded")
    syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "NEARUSDT"]
    s = Settings(mode="replay", symbols=syms, budget_usdt=50, max_positions=2, leverage=3,
                 daily_loss_limit_pct=100, max_drawdown_pct=100, replay_speed=0)
    d5 = pd.read_parquet(raw / "BTCUSDT_5m.parquet", columns=["open_time"])
    market = ReplayMarket(syms, start_ms=int(d5.open_time.iloc[-3000]), speed_s=0, bars=250)
    eng = Engine(s, Strategy(MODEL), market, PaperBroker(), Store(tmp_path / "b.db"))
    peak = []
    orig = eng.on_bar

    async def spy():
        await orig()
        peak.append(sum(p.margin for p in eng.positions.values()))
        assert len(eng.positions) <= 2
    eng.on_bar = spy

    async def go():
        await eng.start()
        await eng.task
    asyncio.run(go())
    assert max(peak) <= 50 + 1e-6 and max(peak) > 0
