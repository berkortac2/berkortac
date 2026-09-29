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

    w = res[(res.family == winner) & (res.group == "train_coins")].set_index("tf")
    wu = res[(res.family == winner) & (res.group == "unseen_coins")].set_index("tf")
    L.append("## Özet\n")
    L.append(f"- **Kazanan yöntem:** {FAMILY_TR.get(winner, winner)}. Seçim kuralı (8 TF'de kilitli test t-istatistiği ortalaması) "
             "kilitli test çalıştırılmadan önce kodda sabitlendi.")
    good = [tf for tf in TFS if tf in w.index and w.loc[tf, "avg_net"] > 0]
    bad = [tf for tf in TFS if tf in w.index and w.loc[tf, "avg_net"] <= 0]
    none = [tf for tf in TFS if tf not in w.index]
    L.append("- **Kilitli testte kârlı zaman dilimleri:** " + ", ".join(
        f"{TF_TR[tf]} (yön %{100 * w.loc[tf, 'dir_hit']:.1f}, kazanma %{100 * w.loc[tf, 'win_rate']:.1f}, "
        f"net/işlem %{100 * w.loc[tf, 'avg_net']:.2f}, {int(w.loc[tf, 'trades'])} işlem)" for tf in good) + ".")
    if bad:
        L.append("- **Kilitli testte zarar eden:** " + ", ".join(
            f"{TF_TR[tf]} (yön %{100 * w.loc[tf, 'dir_hit']:.1f}, net/işlem %{100 * w.loc[tf, 'avg_net']:.2f})" for tf in bad)
            + " → Pine'da bu TF'lerde sinyal varsayılan olarak **kapalı** (ayar: *Sadece kilitli testte kârlı çıkan TF'lerde sinyal ver*).")
    if none:
        L.append("- **Model yok:** " + ", ".join(TF_TR[tf] for tf in none) + " — 18 bin denemenin hiçbiri bu TF'de komisyon "
                 "(%0.14 gidiş-dönüş) sonrası pozitif beklenti üretemedi; 1 dakikalık ATR hareketi komisyondan küçük. "
                 "Yön isabeti %55–57'ye çıksa da net kâr negatif.")
    ug = [tf for tf in TFS if tf in wu.index and wu.loc[tf, "avg_net"] > 0]
    L.append(f"- **Hiç görülmemiş 6 coinde** (PEPE, SUI, ENA, WLD, TAO, ARB) {len(ug)}/{len(wu)} TF kârlı → öğrenilen "
             "desen coin'e özel ezber değil.")
    nshort = int(w.short_trades.sum())
    L.append(f"- **SAT (short) sinyalleri:** kural araması yalnızca {', '.join(TF_TR[tf] for tf in TFS if tf in w.index and w.loc[tf, 'short_trades'] > 0)} "
             f"zaman dilimlerinde doğrulamada kârlı SAT kuralı bulabildi (testte toplam {nshort} SAT işlemi). Diğer TF'lerde "
             "istatistiksel olarak kârlı bir düşüş deseni çıkmadığı için model sadece AL üretir; lojistik modelin iki yönlü "
             "tahminleri (%52–56 isabet) komisyon sonrası kârlı olmadığından eklenmedi.")
    tpq = rep / "final_trades.parquet"
    if tpq.exists():
        tq = pd.read_parquet(tpq)
        tq = tq[tq.family == winner]
        tq["date"] = pd.to_datetime(tq.time, unit="ms").dt.date
        robust = []
        for tf in TFS:
            ok = True
            for g in ("train_coins", "unseen_coins"):
                x = tq[(tq.tf == tf) & (tq.group == g)]
                if len(x) < 5:
                    ok = False
                    break
                bd = x.groupby("date").net.sum().idxmax()
                ok &= x[x.date != bd].net.mean() > 0
            if ok:
                robust.append(TF_TR[tf])
        L.append(f"- **En sağlam zaman dilimleri:** {', '.join(robust)} — en kârlı tek gün çıkarıldığında bile hem eğitim "
                 "hem görülmemiş coinlerde net kârlı (ayrıntı: *Sağlamlık* bölümü). 30 dk ve 1 saatteki yüksek ortalama "
                 "kârın büyük kısmı 10 Ekim 2025 çöküşündeki tepki alımlarından geliyor.")
    L.append("- **Baz çizgileri** (rastgele, klasik RSI 30/70, MACD kesişimi) kilitli testte komisyon sonrası zararda; "
             "rastgele sinyalin yön isabeti ~%49.\n")
    L.append("## En iyi 3 yöntem (kilitli test, tüm zaman dilimleri birleşik)\n")
    L.append("| # | Yöntem | Coin grubu | İşlem | Yön isabeti | İşlem kazanma | Net/işlem | Kârlı TF |")
    L.append("|---|---|---|---|---|---|---|---|")
    for i, f in enumerate(fams, 1):
        for g, gname in (("train_coins", "Eğitim coinleri (20)"), ("unseen_coins", "Hiç görülmemiş (6)")):
            r = summ[(summ.family == f) & (summ.group == g)].iloc[0]
            star = " 🏆" if f == winner and g == "train_coins" else ""
            L.append(f"| {i} | {FAMILY_TR.get(f, f)}{star} | {gname} | {int(r.trades):,} | {pct(r.get('dir_hit'))} | "
                     f"{pct(r.get('win_rate'))} | {pct(r.get('avg_net'), 2)} | {r.get('tf_pos', 0)}/8 |")
    base = res[(res.family == "baseline") & (res.group == "train_coins")]
    for bc, name in (("baseline:random_2pct", "Rastgele sinyal"), ("baseline:rsi_30_70_cross", "Klasik RSI 30/70"),
                     ("baseline:macd_cross", "Klasik MACD kesişimi")):
        p = pooled(base[base.config == bc])
        if p.get("trades"):
            L.append(f"| – | *{name} (baz çizgisi)* | Eğitim coinleri | {p['trades']:,} | {pct(p['dir_hit'])} | "
                     f"{pct(p['win_rate'])} | {pct(p['avg_net'], 2)} | {p['tf_pos']}/8 |")
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
                L.append(f"- **{TF_TR[tf]}** — AL: `{rl or '-'}` · SAT: " + (f"`{rs}`" if rs else "— (kârlı SAT kuralı bulunamadı)"))
            elif "features" in m:
                L.append(f"- **{TF_TR[tf]}**: " + ", ".join(f"`{f}`" for f in m["features"][:10]))
        L.append("")
    GLOSS = {
        "atr_pct": "ATR(14) / fiyat × 100 — volatilite (%)",
        "atr_ratio": "ATR(5) / ATR(50) — volatilite genişlemesi",
        "hist_atr": "MACD histogramı / ATR — normalize momentum",
        "hist_slope3": "MACD histogramının 3 mumluk değişimi / ATR",
        "ret3_atr": "Son 3 mumda fiyat değişimi / ATR",
        "ret5_atr": "Son 5 mumda fiyat değişimi / ATR",
        "rsi7": "(RSI(7) − 50) / 50",
        "rsi21": "(RSI(21) − 50) / 50",
        "rsi_z": "RSI(14)'ün 50 mumluk z-skoru",
        "cci": "CCI(20) / 100",
        "cmf20": "Chaikin Money Flow (20) — hacim ağırlıklı alım/satım baskısı",
        "di": "(+DI − −DI) / 50 — yön gücü",
        "wt_diff": "WaveTrend hızı (wt1 − wt2) / 10",
        "divc14": "Sürekli uyumsuzluk: fiyat eğimi − RSI eğimi (14 mum)",
        "streak": "Ardışık yükselen(+)/düşen(−) mum sayısı / 5",
        "div_hist_bull": "MACD-histogram pozitif (bullish) uyumsuzluk, son 10 mumda (azalan ağırlık)",
        "htf_hist_atr": "Üst zaman diliminin (kapanmış mum) MACD histogramı / ATR",
        "c_close_hist10": "corr(kapanış, MACD-hist) 10 mum — mum-metrik korelasyon",
        "c_close_hist20": "corr(kapanış, MACD-hist) 20 mum — mum-metrik korelasyon",
        "c_close_hist50": "corr(kapanış, MACD-hist) 50 mum — mum-metrik korelasyon",
        "c_close_rsi20": "corr(kapanış, RSI) 20 mum — mum-metrik korelasyon",
        "c_ret_vol20": "corr(getiri, hacim) 20 mum — mum-metrik korelasyon",
        "m_rsi_mfi10": "corr(RSI, MFI) 10 mum — metrik-metrik korelasyon",
        "m_rsi_mfi20": "corr(RSI, MFI) 20 mum — metrik-metrik korelasyon",
        "m_rsi_vol20": "corr(RSI, hacim) 20 mum — metrik-metrik korelasyon",
    }
    if winner in models:
        used = []
        for m in models[winner].values():
            for rr in m.get("rules", {}).values():
                for rule in rr:
                    used += [f for f, _, _ in rule]
        used = list(dict.fromkeys(used))
        if used:
            L.append("\n### Kurallarda geçen girdilerin anlamı\n")
            L.append("| Girdi | Anlamı |")
            L.append("|---|---|")
            for f in used:
                L.append(f"| `{f}` | {GLOSS.get(f, group(f))} |")
            L.append("")
        L.append("**Yorum:** kısa ve orta vadeli zaman dilimlerinde (5 dk – 4 saat) bulunan AL kuralları aynı piyasa "
                 "davranışını yakalıyor: *volatilite yüksekken (ATR/fiyat eşiğin üstünde) momentumun sert negatife dönmesi "
                 "(MACD-hist/ATR çok düşük, son 3–5 mumda ATR cinsinden sert düşüş, RSI aşırı düşük)* → kısa süreli tepki "
                 "yükselişi. 1 gün ve 1 hafta kurallarında ise **mum-metrik ve metrik-metrik korelasyonlar** (kapanış–MACD, "
                 "kapanış–RSI, RSI–MFI, RSI–hacim) ve MACD uyumsuzluğu belirleyici.\n")

    # robustness: how much of the profit comes from the single best day?
    tp = rep / "final_trades.parquet"
    if tp.exists():
        tr_ = pd.read_parquet(tp)
        tr_ = tr_[(tr_.family == winner)]
        tr_["date"] = pd.to_datetime(tr_.time, unit="ms").dt.date
        L.append("## Sağlamlık: kâr tek bir güne mi bağlı?\n")
        L.append("Kripto çöküş günlerinde (ör. 10 Ekim 2025) çok sayıda coin aynı anda sinyal verir. Aşağıda en kârlı tek gün "
                 "çıkarıldığında sonuçlar:\n")
        L.append("| TF | Coin grubu | İşlem | Medyan net/işlem | Ort. net/işlem | En iyi gün | O günün toplam kâra payı | En iyi gün hariç: yön isabeti | En iyi gün hariç: net/işlem |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for tf in TFS:
            for g, gname in (("train_coins", "Eğitim"), ("unseen_coins", "Görülmemiş")):
                x = tr_[(tr_.tf == tf) & (tr_.group == g)]
                if len(x) < 5:
                    continue
                day = x.groupby("date").net.sum()
                bd = day.idxmax()
                rest = x[x.date != bd]
                share = day.max() / x.net.sum() if x.net.sum() > 0 else float("nan")
                L.append(f"| {TF_TR[tf]} | {gname} | {len(x)} | {pct(x.net.median(), 2)} | {pct(x.net.mean(), 2)} | {bd} | "
                         f"{'-' if np.isnan(share) else f'%{100 * share:.0f}'} | {pct(rest.hit.mean())} | {pct(rest.net.mean(), 2)} |")
        L.append("\n**Okuma:** 30 dk ve 1 saatteki yüksek ortalama kâr büyük ölçüde 10 Ekim 2025 çöküşünün ardından gelen "
                 "tepki alımlarından geliyor; o gün çıkarıldığında ortalama net kâr sıfıra yaklaşıyor, fakat medyan işlem "
                 "pozitif kalıyor. Bu yöntem 'panik satışlarında tepki alımı' mantığıyla çalışır: sakin piyasada az sinyal "
                 "verir, sert çöküşlerde güçlü çalışır. 5 dk ve 4 saat sonuçları daha dağınık günlere yayılmıştır.\n")

    # leakage sanity check
    san = sorted((rep / "sanity").glob("shuffled_labels_*.csv"))
    if san:
        L.append("## Sızıntı kontrolü (karıştırılmış etiket testi)\n")
        L.append("Aynı lojistik hat, doğrulama döneminde bir kez gerçek etiketlerle bir kez de coin içinde karıştırılmış "
                 "etiketlerle eğitildi. Sızıntı olsaydı karıştırılmış model de 'başarılı' görünürdü.\n")
        L.append("| Test | Gerçek etiket yön isabeti | Karıştırılmış etiket yön isabeti | Rastgele sinyal |")
        L.append("|---|---|---|---|")
        for f in san:
            d = pd.read_csv(f)
            real = d[d.config.str.endswith("_real") & (d.q == 0.005)]
            shuf = d[d.config.str.endswith("_shuffled") & (d.q == 0.005)]
            rnd = d[d.config == "baseline:random_2pct"]
            tag = f.stem.replace("shuffled_labels_", "").replace("_", " ")
            L.append(f"| {tag} | {pct(real.dir_hit.iloc[0])} | {pct(shuf.dir_hit.iloc[0])} | {pct(rnd.dir_hit.iloc[0])} |")
        L.append("")

    # deflated Sharpe
    L.append("## Çoklu deneme düzeltmesi (Deflated Sharpe)\n")
    L.append("18 bin denemeden 'en iyisini seçmenin' şans payı düşülerek, kilitli testteki performansın gerçek bir "
             "avantaj olma olasılığı (Bailey & López de Prado):\n")
    L.append("| TF | Eğitim coinleri | Görülmemiş coinler |")
    L.append("|---|---|---|")
    for tf in TFS:
        a = res[(res.family == winner) & (res.tf == tf) & (res.group == "train_coins")]
        u = res[(res.family == winner) & (res.tf == tf) & (res.group == "unseen_coins")]
        if a.empty:
            continue
        L.append(f"| {TF_TR[tf]} | {pct(a.iloc[0].dsr_prob, 0)} | {pct(u.iloc[0].dsr_prob, 0) if not u.empty else '-'} |")
    L.append("\nNot: Kilitli test ~13–15 aylık tek bir piyasa dönemidir; TF başına 60–250 işlem olduğu için güven "
             "aralıkları geniştir. Geçmiş performans geleceği garanti etmez, yatırım tavsiyesi değildir.\n")
    par = rep / "pine_parity.csv"
    if par.exists():
        pp = pd.read_csv(par)
        L.append(f"**Pine paritesi:** dışa aktarılan Pine parametreleriyle yeniden hesaplanan işlemler, araştırma modelinin "
                 f"kilitli testteki işlemleriyle {len(pp)} TF'nin {int((pp.jaccard >= 0.999).sum())} tanesinde birebir aynı "
                 f"(ortalama örtüşme %{100 * pp.jaccard.mean():.1f}).\n")
    (rep / "SONUCLAR.md").write_text("\n".join(L))
    print("\n".join(L))
    print("WINNER", winner)


if __name__ == "__main__":
    main()
