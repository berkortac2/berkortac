"""Build reports/SONUCLAR.md from the search trials and the locked final test."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tsa.final import FAMILY_TR, TFS, load_trials  # noqa: E402

TF_TR = {"1m": "1 dk", "5m": "5 dk", "15m": "15 dk", "30m": "30 dk", "1h": "1 saat", "4h": "4 saat", "1d": "1 gün", "1w": "1 hafta"}


def pct(x, d=1):
    return "-" if x is None or (isinstance(x, float) and np.isnan(x)) else f"%{100 * x:.{d}f}"


def pooled(df: pd.DataFrame) -> dict:
    n = df.trades.sum()
    if n == 0:
        return {"trades": 0}
    return {
        "trades": int(n),
        "dir_hit": float((df.dir_hit * df.trades).sum() / n),
        "win_rate": float((df.win_rate * df.trades).sum() / n),
        "avg_net": float((df.avg_net * df.trades).sum() / n),
        "tf_pos": int((df.avg_net > 0).sum()),
        "n_tf": int(len(df)),
    }


def main():
    rep = ROOT / "reports"
    res = pd.read_csv(rep / "final_results.csv")
    ranking = pd.read_csv(rep / "family_ranking_validation.csv")
    trials = load_trials(rep / "search")
    fams = [f for f in ranking.family.tolist() if f in res.family.unique()][:3]

    # winner = best pooled net profit on the locked test (train coins), tie -> direction hit
    summ = []
    for f in fams:
        for g in ("train_coins", "unseen_coins"):
            p = pooled(res[(res.family == f) & (res.group == g)])
            p.update(family=f, group=g)
            summ.append(p)
    summ = pd.DataFrame(summ)
    # pre-registered winner rule (fixed before the locked test was run): highest mean over the 8 TFs of
    # the locked-test t-statistic of net trade returns on the training coins (missing TF = 0),
    # tie-break: pooled direction hit
    keys = {}
    for f in fams:
        t = res[(res.family == f) & (res.group == "train_coins")]
        keys[f] = (t.tstat.fillna(0).sum() / len(TFS), pooled(t).get("dir_hit", 0))
    winner = max(fams, key=lambda f: keys[f])
    summ["test_mean_tstat"] = summ.family.map(lambda f: keys[f][0])
    (rep / "winner.json").write_text(json.dumps({"family": winner}, indent=1))

    L = []
    L.append("# Tradingview Signal Agent — Sonuç Raporu\n")
    L.append(f"Toplam denenen strateji kombinasyonu (trial): **{len(trials):,}** "
             f"(8 zaman dilimi × 6 yöntem ailesi × feature setleri × ufuk H × TP/SL × eşik).  ")
    L.append("Tüm yüzdeler **kilitli test döneminden** (aramanın hiç görmediği son ~%20'lik zaman dilimi) ve "
             "komisyon (%0.05/taraf) + kayma (%0.02/taraf) **dahil** hesaplanmıştır.\n")
    L.append("**Tanımlar** — *Yön isabeti*: sinyal yönünde, H mum sonra kapanış giriş fiyatının doğru tarafında mı. "
             "*İşlem kazanma*: ATR tabanlı TP/SL + zaman çıkışlı işlem komisyon sonrası kârla mı kapandı. "
             "*Net/işlem*: komisyon sonrası ortalama işlem getirisi.\n")

    L.append("## En iyi 3 yöntem (kilitli test, tüm zaman dilimleri birleşik)\n")
    L.append("| # | Yöntem | Coin grubu | İşlem | Yön isabeti | İşlem kazanma | Net/işlem | Kârlı TF |")
    L.append("|---|---|---|---|---|---|---|---|")
    for i, f in enumerate(fams, 1):
        for g, gname in (("train_coins", "Eğitim coinleri (20)"), ("unseen_coins", "Hiç görülmemiş (6)")):
            r = summ[(summ.family == f) & (summ.group == g)].iloc[0]
            star = " 🏆" if f == winner and g == "train_coins" else ""
            L.append(f"| {i} | {FAMILY_TR.get(f, f)}{star} | {gname} | {int(r.trades):,} | {pct(r.get('dir_hit'))} | "
                     f"{pct(r.get('win_rate'))} | {pct(r.get('avg_net'), 2)} | {r.get('tf_pos', 0)}/{r.get('n_tf', 0)} |")
    base = res[(res.family == "baseline") & (res.group == "train_coins")]
    for bc, name in (("baseline:random_2pct", "Rastgele sinyal"), ("baseline:rsi_30_70_cross", "Klasik RSI 30/70"),
                     ("baseline:macd_cross", "Klasik MACD kesişimi")):
        p = pooled(base[base.config == bc])
        if p.get("trades"):
            L.append(f"| – | *{name} (baz çizgisi)* | Eğitim coinleri | {p['trades']:,} | {pct(p['dir_hit'])} | "
                     f"{pct(p['win_rate'])} | {pct(p['avg_net'], 2)} | {p['tf_pos']}/{p['n_tf']} |")
    L.append("")

    for f in fams:
        L.append(f"## {FAMILY_TR.get(f, f)} — zaman dilimi bazında\n")
        L.append("| TF | Ayar | H | TP/SL (ATR) | İşlem | Yön isabeti (%95 GA) | AL isabet | SAT isabet | İşlem kazanma | Net/işlem | PF | Görülmemiş coin yön isabeti | Doğrulama yön isabeti |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for tf in TFS:
            a = res[(res.family == f) & (res.tf == tf) & (res.group == "train_coins")]
            u = res[(res.family == f) & (res.tf == tf) & (res.group == "unseen_coins")]
            if a.empty:
                continue
            r = a.iloc[0]
            tpsl = "zaman çıkışı" if pd.isna(r.tp_atr) else f"{r.tp_atr:g}/{r.sl_atr:g}"
            ci = f"{pct(r.dir_hit)} ({pct(r.dir_hit_lo, 0)}–{pct(r.dir_hit_hi, 0)})" if r.trades > 0 else "-"
            un = pct(u.iloc[0].dir_hit) + f" ({int(u.iloc[0].trades)} işlem)" if not u.empty and u.iloc[0].trades > 0 else "-"
            L.append(f"| {TF_TR[tf]} | `{r.config}` | {int(r.H)} | {tpsl} | {int(r.trades)} | {ci} | "
                     f"{pct(r.get('long_dir_hit'))} | {pct(r.get('short_dir_hit'))} | {pct(r.win_rate)} | "
                     f"{pct(r.avg_net, 2)} | {r.pf:.2f} | {un} | {pct(r.val_dir_hit)} |")
        L.append("")

    # validation ranking
    L.append("## Doğrulama (walk-forward) sıralaması — tüm aileler\n")
    L.append("| Aile | Ortalama skor (t-istatistiği × kat istikrarı) | Kârlı TF | Ortalama yön isabeti |")
    L.append("|---|---|---|---|")
    for r in ranking.itertuples():
        L.append(f"| {FAMILY_TR.get(r.family, r.family)} | {r.mean_score:.2f} | {r.tf_positive}/{r.n_tf} | {pct(r.mean_dir_hit)} |")
    L.append("")

    # ablation: do correlations help?
    L.append("## Korelasyon katmanları işe yarıyor mu? (lojistik model, doğrulama dönemi)\n")
    L.append("Aynı model, özellik katmanları eklenerek: momentum → +mum+hacim → +uyumsuzluk → +mum/metrik korelasyonları → "
             "+korelasyonun korelasyonu → +zaman/üst TF. Hücre: o TF'deki en iyi denemenin skoru / yön isabeti.\n")
    order = ["mom", "mom_cdl_vol", "mom_cdl_vol_div", "plus_corr", "plus_corr2", "all"]
    names = {"mom": "Momentum", "mom_cdl_vol": "+Mum+Hacim", "mom_cdl_vol_div": "+Divergence",
             "plus_corr": "+Korelasyon", "plus_corr2": "+Korelasyonun kor.", "all": "+Zaman+ÜstTF"}
    L.append("| TF | " + " | ".join(names[o] for o in order) + " |")
    L.append("|---|" + "---|" * len(order))
    for tf in TFS:
        t = trials[(trials.tf == tf) & (trials.family == "logit")]
        if t.empty:
            continue
        cells = []
        for o in order:
            tt = t[t.config == f"logit:{o}:C=0.05"]
            if tt.empty:
                cells.append("-")
                continue
            b = tt.sort_values("score", ascending=False).iloc[0]
            cells.append(f"{b.score:.2f} / {pct(b.dir_hit)}")
        L.append(f"| {TF_TR[tf]} | " + " | ".join(cells) + " |")
    L.append("")

    # which inputs survive sparse selection most often (all TFs x horizons, walk-forward last fold)
    from collections import Counter
    cnt, gimp = Counter(), Counter()
    n_lists = 0
    for p in sorted((rep / "search").glob("artifacts_*.json")):
        a = json.loads(p.read_text())
        for k, v in a.items():
            if k.startswith("logit_top20_H"):
                cnt.update(v["features"])
                n_lists += 1
            if k.startswith("gbm_importance_H"):
                tot = sum(x[1] for x in v) or 1.0
                for f, g in v:
                    gimp[f] += g / tot
    def group(f):
        if f.startswith(("cc_", "d5_", "z_")):
            return "korelasyonun korelasyonu"
        if f.startswith("c_"):
            return "mum-metrik korelasyon"
        if f.startswith("m_"):
            return "metrik-metrik korelasyon"
        if f.startswith("div"):
            return "uyumsuzluk"
        if f.startswith("htf_"):
            return "üst TF"
        if f in ("hour_sin", "hour_cos", "dow_sin", "dow_cos"):
            return "zaman"
        if f in ("relvol", "vol_z", "svflow10", "svflow30", "cmf20", "vol_trend"):
            return "hacim"
        if f in ("body", "upwick", "lowwick", "clv", "range_atr", "ret1_atr", "ret3_atr", "ret5_atr", "ret10_atr",
                 "streak", "engulf", "pin", "chan20", "chan50", "chan100"):
            return "mum"
        return "momentum/volatilite"
    if n_lists:
        L.append("## Hangi girdiler gerçekten işe yarıyor?\n")
        L.append(f"Seyrek (top-20) lojistik modelin {n_lists} ayrı eğitiminde (8 TF × ufuklar) en sık seçilen girdiler "
                 "ve LightGBM kazanç (gain) payı:\n")
        L.append("| Girdi | Grup | Top-20'de seçilme | LightGBM kazanç payı (toplam) |")
        L.append("|---|---|---|---|")
        for f, c in cnt.most_common(20):
            L.append(f"| `{f}` | {group(f)} | {c}/{n_lists} | {gimp.get(f, 0):.2f} |")
        gc = Counter()
        for f, c in cnt.items():
            gc[group(f)] += c
        tot = sum(gc.values())
        L.append("\nGrup bazında top-20 koltuk payı: " + ", ".join(f"{g} **%{100 * c / tot:.0f}**" for g, c in gc.most_common()) + "\n")

    # most important features of the winner
    models = json.loads((rep / "final_models.json").read_text())
    if winner in models:
        L.append(f"## Kazanan yöntemin en etkili girdileri ({FAMILY_TR.get(winner, winner)})\n")
        for tf in TFS:
            m = models[winner].get(tf)
            if not m:
                continue
            if "w" in m:
                pairs = sorted(zip(m["features"], m["w"]), key=lambda x: -abs(x[1]))[:8]
                L.append(f"- **{TF_TR[tf]}**: " + ", ".join(f"`{f}` ({w:+.2f})" for f, w in pairs))
            elif "rules" in m:
                rl = " VEYA ".join(" & ".join(f"{f} {op} {v:.3g}" for f, op, v in r) for r in m["rules"]["1"])
                rs = " VEYA ".join(" & ".join(f"{f} {op} {v:.3g}" for f, op, v in r) for r in m["rules"]["-1"])
                L.append(f"- **{TF_TR[tf]}** — AL: `{rl}` · SAT: `{rs}`")
            elif "features" in m:
                L.append(f"- **{TF_TR[tf]}**: " + ", ".join(f"`{f}`" for f in m["features"][:10]))
        L.append("")
    (rep / "SONUCLAR.md").write_text("\n".join(L))
    print("\n".join(L))
    print("WINNER", winner)


if __name__ == "__main__":
    main()
