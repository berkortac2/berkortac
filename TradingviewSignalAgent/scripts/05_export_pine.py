"""Write the Pine v6 indicator and strategy for the best family.

python scripts/05_export_pine.py [--family logit]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tsa.export.pine_codegen import generate  # noqa: E402
from tsa.final import FAMILY_TR  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default="")
    args = ap.parse_args()
    models = json.loads((ROOT / "reports" / "final_models.json").read_text())
    res = pd.read_csv(ROOT / "reports" / "final_results.csv")
    fam = args.family or json.loads((ROOT / "reports" / "winner.json").read_text())["family"]
    tr = res[(res.family == fam) & (res.group == "train_coins")]
    stats = {r.tf: {"dir_hit": r.dir_hit, "win_rate": r.win_rate, "trades": r.trades, "avg_net": r.avg_net}
             for r in tr.itertuples()}
    m5 = models[fam].get("5m", {})
    if str(m5.get("config", "")).startswith("deep5m:"):   # second-round 5m model: its own locked-test row
        d = pd.read_csv(ROOT / "reports" / "deep5m_final_results.csv")
        r = d[(d.group == "search12") & (d.model == m5["config"].split(":")[1]) & (d.fee == "futures_taker")].iloc[0]
        stats["5m"] = {"dir_hit": r.dir_hit, "win_rate": r.win_rate, "trades": r.trades, "avg_net": r.avg_net}
    if "stats_override" in m5:   # 5m with the validated exit policy (reports/exit_study_5m_summary.csv)
        stats["5m"] = m5["stats_override"]
    label = FAMILY_TR.get(fam, fam)
    out = ROOT / "pine"
    out.mkdir(exist_ok=True)
    (out / "TradingviewSignalAgent.pine").write_text(generate(models[fam], stats, label, strategy=False))
    (out / "TradingviewSignalAgent_Strategy.pine").write_text(generate(models[fam], stats, label, strategy=True))
    print("written", fam, sorted(models[fam]))


if __name__ == "__main__":
    main()
