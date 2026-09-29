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
