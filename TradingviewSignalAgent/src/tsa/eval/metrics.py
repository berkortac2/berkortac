"""Trade / signal metrics."""
from __future__ import annotations

import math

import numpy as np
from scipy import stats


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - r) / d, (c + r) / d)


def trade_metrics(dirs: np.ndarray, nets: np.ndarray, hits: np.ndarray, n_bars: int,
                  bars_per_year: float, n_coins: int = 1) -> dict:
    n = int(len(nets))
    out = {"trades": n, "signals_per_100_bars": 100.0 * n / max(n_bars, 1)}
    if n == 0:
        return out
    wins = int((nets > 0).sum())
    dh = int(np.nansum(hits))
    out.update(
        dir_hit=dh / n, dir_hit_lo=wilson(dh, n)[0], dir_hit_hi=wilson(dh, n)[1],
        win_rate=wins / n, win_lo=wilson(wins, n)[0], win_hi=wilson(wins, n)[1],
        avg_net=float(nets.mean()), med_net=float(np.median(nets)),
        pf=float(nets[nets > 0].sum() / max(-nets[nets < 0].sum(), 1e-12)),
        tstat=float(nets.mean() / (nets.std(ddof=1) + 1e-12) * math.sqrt(n)) if n > 1 else 0.0,
        long_trades=int((dirs > 0).sum()), short_trades=int((dirs < 0).sum()),
    )
    for name, m in (("long", dirs > 0), ("short", dirs < 0)):
        k = int(m.sum())
        out[f"{name}_dir_hit"] = float(np.nansum(hits[m]) / k) if k else float("nan")
        out[f"{name}_win_rate"] = float((nets[m] > 0).mean()) if k else float("nan")
        out[f"{name}_avg_net"] = float(nets[m].mean()) if k else float("nan")
    # annualised Sharpe of the per-coin equal-weight trade stream
    trades_per_year = n / max(n_bars / bars_per_year, 1e-9) / max(n_coins, 1)
    out["sharpe"] = float(nets.mean() / (nets.std(ddof=1) + 1e-12) * math.sqrt(max(trades_per_year, 1e-9))) if n > 1 else 0.0
    eq = np.cumsum(nets)
    out["max_dd_sum"] = float((np.maximum.accumulate(eq) - eq).max())
    return out


def deflated_sharpe_prob(sr: float, n_obs: int, n_trials: int, sr_var: float,
                         skew: float = 0.0, kurt: float = 3.0) -> float:
    """Bailey & Lopez de Prado (2014) deflated Sharpe ratio probability.

    sr is the per-observation (per-trade) Sharpe of the selected strategy,
    sr_var the variance of per-trade Sharpe across all trials."""
    if n_obs < 3 or n_trials < 1:
        return float("nan")
    emc = 0.5772156649
    sr0 = math.sqrt(max(sr_var, 1e-12)) * ((1 - emc) * stats.norm.ppf(1 - 1.0 / n_trials)
                                           + emc * stats.norm.ppf(1 - 1.0 / (n_trials * math.e)))
    num = (sr - sr0) * math.sqrt(n_obs - 1)
    den = math.sqrt(max(1 - skew * sr + (kurt - 1) / 4 * sr * sr, 1e-12))
    return float(stats.norm.cdf(num / den))
