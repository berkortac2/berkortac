"""5m scanner: 1h context rebuilt from 5m candles == research HTF features; Pine limits."""
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tsa.export.pine_scanner import generate_scanner, hourly_mirror
from tsa.features.registry import compute_features

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"


def test_hourly_mirror_equals_research_htf_features():
    if not (RAW / "NEARUSDT_5m.parquet").exists():
        pytest.skip("data not downloaded")
    d5 = pd.read_parquet(RAW / "NEARUSDT_5m.parquet").iloc[-12000:].reset_index(drop=True)
    d1 = pd.read_parquet(RAW / "NEARUSDT_1h.parquet")
    d1 = d1[d1.open_time >= d5.open_time.iloc[0]].reset_index(drop=True)
    m, f = hourly_mirror(d5), compute_features(d5, "5m", d1)
    for c in m.columns:
        a, b = m[c].to_numpy()[6000:], f[c].to_numpy()[6000:]
        assert np.nanmax(np.abs(a - b)) < 1e-9, c


def test_scanner_code_limits():
    model = {"long": {"rules": [[["di", "<=", -0.4], ["htf_hist_atr", "<=", -0.1]]], "H": 24},
             "short": {"rules": [[["rsi7", ">=", 0.5]]], "H": 12}}
    code = generate_scanner(model)
    assert code.startswith("//@version=6")
    assert len(re.findall(r"request\.security\(", code)) <= 40          # TradingView unique request limit
    assert "? na :" not in code and "nz(true" not in code
    assert "hourlyCtx()" in code and "x_htf_hist_atr" in code and "x_rsi7" in code
    # every function is declared before it is called
    defs = [m.start() for m in re.finditer(r"^(\w+)\(.*\) =>", code, re.M)]
    names = re.findall(r"^(\w+)\(.*\) =>", code, re.M)
    for n, pos in zip(names, defs):
        first_call = re.search(rf"(?<![\w.]){n}\(", code[:pos])
        assert first_call is None, n


def scanner_port(o, h, l, c, atr, sig, x, H, sl, em):
    """Line-by-line Python port of the scanner's upd() for one coin (AL only)."""
    d, en, st, bars, a_sig, nets = 0, np.nan, np.nan, 0, np.nan, []
    for k in range(len(o)):                     # candle k has just closed
        if d != 0:
            if np.isnan(en):                    # entry = open of the candle after the signal
                en = o[k]
                st = en * (1 - em) if em > 0 else np.nan
                if sl > 0 and not np.isnan(a_sig):
                    s2 = en - sl * a_sig
                    st = s2 if np.isnan(st) else max(st, s2)
            bars += 1
            px = np.nan
            if not np.isnan(st) and l[k] <= st:
                px = st if bars == 1 else min(st, o[k])
            elif x[k] > 0:
                px = c[k]
            elif bars >= H:
                px = c[k]
            if not np.isnan(px):
                nets.append(px / en - 1.0 - 0.0014)
                d = 0
        if d == 0 and sig[k]:
            d, en, st, a_sig, bars = 1, np.nan, np.nan, atr[k], 0
    return np.array(nets)


def test_scanner_position_logic_equals_research_simulator():
    if not (RAW / "NEARUSDT_5m.parquet").exists():
        pytest.skip("data not downloaded")
    from tsa import exits as es
    from tsa.indicators import pine_ta as ta
    d5 = pd.read_parquet(RAW / "NEARUSDT_5m.parquet").iloc[-30000:].reset_index(drop=True)
    d1 = pd.read_parquet(RAW / "NEARUSDT_1h.parquet")
    f = compute_features(d5, "5m", d1)
    o, h, l, c = (d5[k].to_numpy() for k in ("open", "high", "low", "close"))
    atr = ta.atr(h, l, c, 14)
    sig = (f["rsi7"].to_numpy() <= -0.35) & np.isfinite(atr)          # frequent rule -> many trades
    sig[:200] = False
    x = (f["wt"].fillna(0).to_numpy() >= 1.0).astype(float)
    F = np.ascontiguousarray(f[es.EXIT_FEATS].fillna(0).to_numpy(np.float64))
    ref, _, _ = es.sim(o, h, l, c, atr, sig, F, np.array([0]), np.array([len(o)]), 96, 0.0, 3.0, 0.08,
                       0.0, 0.0, es.EXIT_FEATS.index("wt"), 1.0, 0.0014)
    got = scanner_port(o, h, l, c, atr, sig, x, 96, 3.0, 0.08)
    assert len(ref) > 100
    k = min(len(ref), len(got))
    assert abs(len(ref) - len(got)) <= 1                             # last trade may be open
    assert np.max(np.abs(ref[:k] - got[:k])) < 1e-12
