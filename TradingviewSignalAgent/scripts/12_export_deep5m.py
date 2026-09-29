"""Export the pre-registered deep-5m model everywhere it is used:
  * bot/model/model_5m.json         (bot)
  * reports/final_models.json [rules][5m] (+ per-direction H/TP/SL) -> pine/TradingviewSignalAgent*.pine
  * pine/TradingviewSignalAgent_Scanner5m.pine
The previous 5m entry is kept in reports/final_models_v1_5m.json.
Only runs when the chosen model beat v1 on the locked test of the search coins AND on the
never-used hold-out coins (both after taker fees); otherwise v1 stays and this is reported.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REP = ROOT / "reports"


def main():
    sel = json.loads((REP / "deep5m_models.json").read_text())
    res = pd.read_csv(REP / "deep5m_final_results.csv")
    chosen = sel["chosen"]
    m = sel["models"][chosen]
    tk = res[res.fee == "futures_taker"].set_index(["group", "model"])

    def g(group, model, col):
        try:
            return float(tk.loc[(group, model), col])
        except KeyError:
            return float("nan")
    beats = all(g(grp, chosen, "avg_net") > max(0.0, g(grp, "v1", "avg_net") if g(grp, "v1", "trades") >= 20 else 0.0)
                or g(grp, chosen, "net_per_coin_day") > g(grp, "v1", "net_per_coin_day")
                for grp in ("search12", "holdout10"))
    positive = g("search12", chosen, "avg_net") > 0 and g("holdout10", chosen, "avg_net") > 0
    print("chosen", chosen, "beats v1:", beats, "positive:", positive)
    if not (beats and positive):
        print("keeping v1 5m model")
        return
    rows = []
    for grp in ("search12", "other_train8", "unseen6", "holdout10", "holdout10_pre"):
        for side, col in (("toplam", ""), ("long", "long_"), ("short", "short_")):
            if (grp, chosen) not in tk.index:
                continue
            r = tk.loc[(grp, chosen)]
            n = r["trades"] if side == "toplam" else r[f"{side}_trades"]
            if not n or n != n:
                continue
            rows.append({"group": grp, "side": side, "trades": int(n),
                         "dir_hit": float(r[f"{col}dir_hit"]), "win_rate": float(r[f"{col}win_rate"]),
                         "avg_net": float(r[f"{col}avg_net"]),
                         "trades_per_coin_day": float(r["trades_per_coin_day"]) if side == "toplam" else None})
    bot_model = {"name": f"TSA 5m kural modeli (v2, derin arama: {chosen})", "tf": "5m", "version": 2,
                 "long": {k: m["long"][k] for k in ("rules", "H", "tp_atr", "sl_atr")},
                 "short": {k: m["short"][k] for k in ("rules", "H", "tp_atr", "sl_atr")},
                 "cost_rt": 0.0014, "stats": {"rows": rows}}
    (ROOT / "bot" / "model" / "model_5m.json").write_text(json.dumps(bot_model, indent=1))

    fm = json.loads((REP / "final_models.json").read_text())
    v1p = REP / "final_models_v1_5m.json"
    if not v1p.exists():
        v1p.write_text(json.dumps(fm["rules"]["5m"], indent=1))
    L, S = m["long"], m["short"]
    fm["rules"]["5m"] = {"tf": "5m", "family": "rules", "config": f"deep5m:{chosen}", "H": L["H"],
                         "tp_atr": L["tp_atr"], "sl_atr": L["sl_atr"], "q": None,
                         "H_short": S["H"], "tp_atr_short": S["tp_atr"], "sl_atr_short": S["sl_atr"],
                         "rules": {"1": L["rules"], "-1": S["rules"]}}
    (REP / "final_models.json").write_text(json.dumps(fm, indent=1, default=float))
    for script in ("05_export_pine.py", "11_export_scanner.py"):
        subprocess.run([sys.executable, str(ROOT / "scripts" / script)], check=True)
    print("exported", chosen)


if __name__ == "__main__":
    main()
