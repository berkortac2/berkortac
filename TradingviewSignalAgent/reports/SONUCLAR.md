# Tradingview Signal Agent — Sonuç Raporu

Toplam denenen strateji kombinasyonu (trial): **18,466** (8 zaman dilimi × 6 yöntem ailesi × feature setleri × ufuk H × TP/SL × eşik).  
Tüm yüzdeler **kilitli test döneminden** (aramanın hiç görmediği son ~%20'lik zaman dilimi) ve komisyon (%0.05/taraf) + kayma (%0.02/taraf) **dahil** hesaplanmıştır.

**Tanımlar** — *Yön isabeti*: sinyal yönünde, H mum sonra kapanış giriş fiyatının doğru tarafında mı. *İşlem kazanma*: ATR tabanlı TP/SL + zaman çıkışlı işlem komisyon sonrası kârla mı kapandı. *Net/işlem*: komisyon sonrası ortalama işlem getirisi.

## Özet

- **Kazanan yöntem:** Kural/konfluans araması (RSI-MACD-divergence-hacim-korelasyon). Seçim kuralı (8 TF'de kilitli test t-istatistiği ortalaması) kilitli test çalıştırılmadan önce kodda sabitlendi.
- **Kilitli testte kârlı zaman dilimleri:** 5 dk (yön %61.2, kazanma %58.5, net/işlem %0.46, 183 işlem), 30 dk (yön %54.0, kazanma %54.0, net/işlem %2.95, 137 işlem), 1 saat (yön %56.5, kazanma %55.4, net/işlem %0.39, 92 işlem), 4 saat (yön %52.8, kazanma %50.8, net/işlem %0.50, 250 işlem), 1 hafta (yön %65.5, kazanma %63.8, net/işlem %1.18, 58 işlem).
- **Kilitli testte zarar eden:** 15 dk (yön %41.7, net/işlem %-0.92), 1 gün (yön %47.7, net/işlem %-0.93) → Pine'da bu TF'lerde sinyal varsayılan olarak **kapalı** (ayar: *Sadece kilitli testte kârlı çıkan TF'lerde sinyal ver*).
- **Model yok:** 1 dk — 18 bin denemenin hiçbiri bu TF'de komisyon (%0.14 gidiş-dönüş) sonrası pozitif beklenti üretemedi; 1 dakikalık ATR hareketi komisyondan küçük. Yön isabeti %55–57'ye çıksa da net kâr negatif.
- **Hiç görülmemiş 6 coinde** (PEPE, SUI, ENA, WLD, TAO, ARB) 6/7 TF kârlı → öğrenilen desen coin'e özel ezber değil.
- **SAT (short) sinyalleri:** kural araması yalnızca 4 saat, 1 gün, 1 hafta zaman dilimlerinde doğrulamada kârlı SAT kuralı bulabildi (testte toplam 194 SAT işlemi). Diğer TF'lerde istatistiksel olarak kârlı bir düşüş deseni çıkmadığı için model sadece AL üretir; lojistik modelin iki yönlü tahminleri (%52–56 isabet) komisyon sonrası kârlı olmadığından eklenmedi.
- **En sağlam zaman dilimleri:** 5 dk, 30 dk, 4 saat, 1 hafta — en kârlı tek gün çıkarıldığında bile hem eğitim hem görülmemiş coinlerde net kârlı (ayrıntı: *Sağlamlık* bölümü). 30 dk ve 1 saatteki yüksek ortalama kârın büyük kısmı 10 Ekim 2025 çöküşündeki tepki alımlarından geliyor.
- **Baz çizgileri** (rastgele, klasik RSI 30/70, MACD kesişimi) kilitli testte komisyon sonrası zararda; rastgele sinyalin yön isabeti ~%49.

## En iyi 3 yöntem (kilitli test, tüm zaman dilimleri birleşik)

| # | Yöntem | Coin grubu | İşlem | Yön isabeti | İşlem kazanma | Net/işlem | Kârlı TF |
|---|---|---|---|---|---|---|---|
| 1 | Kural/konfluans araması (RSI-MACD-divergence-hacim-korelasyon) 🏆 | Eğitim coinleri (20) | 1,082 | %53.0 | %51.6 | %0.35 | 5/8 |
| 1 | Kural/konfluans araması (RSI-MACD-divergence-hacim-korelasyon) | Hiç görülmemiş (6) | 627 | %56.0 | %54.2 | %0.65 | 6/8 |
| 2 | Meta-labeling (kural + model onayı) | Eğitim coinleri (20) | 1,573 | %50.3 | %49.1 | %0.18 | 5/8 |
| 2 | Meta-labeling (kural + model onayı) | Hiç görülmemiş (6) | 740 | %53.9 | %52.4 | %0.50 | 5/8 |
| 3 | Lojistik skor modeli (korelasyon katmanlı) | Eğitim coinleri (20) | 71,278 | %53.7 | %34.5 | %-0.13 | 3/8 |
| 3 | Lojistik skor modeli (korelasyon katmanlı) | Hiç görülmemiş (6) | 19,732 | %54.9 | %43.9 | %-0.12 | 3/8 |
| – | *Rastgele sinyal (baz çizgisi)* | Eğitim coinleri | 198,568 | %48.9 | %24.5 | %-0.14 | 2/8 |
| – | *Klasik RSI 30/70 (baz çizgisi)* | Eğitim coinleri | 132,491 | %51.1 | %27.2 | %-0.15 | 0/8 |
| – | *Klasik MACD kesişimi (baz çizgisi)* | Eğitim coinleri | 373,609 | %47.8 | %22.6 | %-0.14 | 1/8 |

## Kural/konfluans araması (RSI-MACD-divergence-hacim-korelasyon) — zaman dilimi bazında

| TF | Ayar | H | TP/SL (ATR) | İşlem | Yön isabeti (%95 GA) | AL isabet | SAT isabet | İşlem kazanma | Net/işlem | PF | Görülmemiş coin yön isabeti | Doğrulama yön isabeti |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 dk | `rules:top1` | 24 | zaman çıkışı | 183 | %61.2 (%54–%68) | %61.2 | - | %58.5 | %0.46 | 1.73 | %61.2 (121 işlem) | %55.1 |
| 15 dk | `rules:top1` | 16 | zaman çıkışı | 127 | %41.7 (%34–%50) | %41.7 | - | %40.2 | %-0.92 | 0.56 | %50.5 (111 işlem) | %67.1 |
| 30 dk | `rules:top3_or` | 4 | zaman çıkışı | 137 | %54.0 (%46–%62) | %54.0 | - | %54.0 | %2.95 | 3.70 | %57.3 (103 işlem) | %67.6 |
| 1 saat | `rules:top1` | 3 | zaman çıkışı | 92 | %56.5 (%46–%66) | %56.5 | - | %55.4 | %0.39 | 1.37 | %58.4 (89 işlem) | %58.9 |
| 4 saat | `rules:top3_or` | 6 | zaman çıkışı | 250 | %52.8 (%47–%59) | %57.9 | %49.7 | %50.8 | %0.50 | 1.36 | %59.3 (118 işlem) | %54.4 |
| 1 gün | `rules:top1` | 10 | zaman çıkışı | 235 | %47.7 (%41–%54) | %48.1 | %25.0 | %47.2 | %-0.93 | 0.82 | %47.7 (65 işlem) | %51.7 |
| 1 hafta | `rules:top3_or` | 2 | 2/1 | 58 | %65.5 (%53–%76) | %73.9 | %60.0 | %63.8 | %1.18 | 1.39 | %45.0 (20 işlem) | %54.3 |

## Meta-labeling (kural + model onayı) — zaman dilimi bazında

| TF | Ayar | H | TP/SL (ATR) | İşlem | Yön isabeti (%95 GA) | AL isabet | SAT isabet | İşlem kazanma | Net/işlem | PF | Görülmemiş coin yön isabeti | Doğrulama yön isabeti |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 dk | `meta:rule_top1&logit_agree` | 24 | zaman çıkışı | 178 | %61.2 (%54–%68) | %61.2 | - | %58.4 | %0.49 | 1.77 | %61.2 (121 işlem) | %55.2 |
| 15 dk | `meta:rule_top1&logit_agree` | 16 | zaman çıkışı | 127 | %41.7 (%34–%50) | %41.7 | - | %39.4 | %-0.89 | 0.56 | %50.5 (109 işlem) | %67.1 |
| 30 dk | `meta:rule_top1&logit_agree` | 4 | zaman çıkışı | 108 | %59.3 (%50–%68) | %59.3 | - | %59.3 | %3.77 | 4.55 | %57.3 (82 işlem) | %71.5 |
| 1 saat | `meta:rule_top1&logit_agree` | 3 | zaman çıkışı | 88 | %58.0 (%48–%68) | %58.0 | - | %56.8 | %0.48 | 1.47 | %58.0 (88 işlem) | %59.4 |
| 4 saat | `meta:rule_top1&logit_agree` | 6 | zaman çıkışı | 139 | %55.4 (%47–%63) | %57.3 | %53.1 | %54.0 | %0.57 | 1.35 | %60.9 (92 işlem) | %55.7 |
| 1 gün | `meta:rule_top1&logit_agree` | 2 | 1.5/1.5 | 928 | %46.9 (%44–%50) | %46.1 | %52.1 | %46.0 | %-0.23 | 0.89 | %47.0 (247 işlem) | %57.4 |
| 1 hafta | `meta:rule_top1&logit_agree` | 2 | 2/1 | 5 | %60.0 (%23–%88) | %60.0 | - | %60.0 | %0.10 | 1.04 | %0.0 (1 işlem) | %55.9 |

## Lojistik skor modeli (korelasyon katmanlı) — zaman dilimi bazında

| TF | Ayar | H | TP/SL (ATR) | İşlem | Yön isabeti (%95 GA) | AL isabet | SAT isabet | İşlem kazanma | Net/işlem | PF | Görülmemiş coin yön isabeti | Doğrulama yön isabeti |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 dk | `logit:mom:C=0.05` | 10 | 1/1 | 28350 | %54.9 (%54–%55) | %55.2 | %54.5 | %19.0 | %-0.14 | 0.14 | %57.2 (7355 işlem) | %56.6 |
| 5 dk | `logit:all_top20:C=0.05` | 24 | zaman çıkışı | 31921 | %52.8 (%52–%53) | %52.4 | %53.1 | %43.3 | %-0.14 | 0.66 | %53.0 (9299 işlem) | %53.3 |
| 15 dk | `logit:all:C=0.05` | 16 | zaman çıkışı | 4729 | %54.4 (%53–%56) | %56.8 | %52.4 | %48.7 | %-0.05 | 0.92 | %55.9 (1407 işlem) | %59.3 |
| 30 dk | `logit:all:C=0.05` | 16 | zaman çıkışı | 4267 | %52.0 (%50–%53) | %51.5 | %52.3 | %48.3 | %-0.15 | 0.83 | %55.8 (1189 işlem) | %56.8 |
| 1 saat | `logit:all_top20:C=0.05` | 12 | zaman çıkışı | 1061 | %56.4 (%53–%59) | %57.4 | %55.5 | %53.1 | %0.12 | 1.13 | %54.2 (253 işlem) | %56.9 |
| 4 saat | `logit:mom:C=0.05` | 3 | zaman çıkışı | 336 | %54.5 (%49–%60) | %60.0 | %51.0 | %50.3 | %0.22 | 1.20 | %61.4 (57 işlem) | %58.2 |
| 1 gün | `logit:plus_corr:C=0.05` | 2 | zaman çıkışı | 552 | %49.8 (%46–%54) | %46.3 | %58.3 | %48.6 | %-0.37 | 0.83 | %44.8 (163 işlem) | %55.8 |
| 1 hafta | `logit:all:C=0.05` | 4 | 1.5/1.5 | 62 | %51.6 (%39–%64) | %45.3 | %88.9 | %51.6 | %0.66 | 1.10 | %66.7 (9 işlem) | %57.9 |

## Doğrulama (walk-forward) sıralaması — tüm aileler

| Aile | Ortalama skor (t-istatistiği × kat istikrarı) | Kârlı TF | Ortalama yön isabeti |
|---|---|---|---|
| Kural/konfluans araması (RSI-MACD-divergence-hacim-korelasyon) | 3.78 | 7/7 | %58.5 |
| Meta-labeling (kural + model onayı) | 3.66 | 7/7 | %60.3 |
| Lojistik skor modeli (korelasyon katmanlı) | 2.45 | 6/8 | %56.8 |
| Sığ Gradient Boosting (LightGBM) | 2.00 | 5/8 | %56.6 |
| Online lojistik (her mumda ağırlık güncelleyen) | 0.75 | 3/8 | %55.9 |
| Lorentzian kNN (mum-mum kendi kendine öğrenen) | 0.34 | 3/8 | %52.0 |

## Korelasyon katmanları işe yarıyor mu? (lojistik model, doğrulama dönemi)

Aynı model, özellik katmanları eklenerek: momentum → +mum+hacim → +uyumsuzluk → +mum/metrik korelasyonları → +korelasyonun korelasyonu → +zaman/üst TF. Hücre: o TF'deki en iyi denemenin skoru / yön isabeti.

| TF | Momentum | +Mum+Hacim | +Divergence | +Korelasyon | +Korelasyonun kor. | +Zaman+ÜstTF |
|---|---|---|---|---|---|---|
| 1 dk | -0.00 / %56.6 | -0.00 / %56.5 | -0.00 / %56.2 | -0.00 / %56.2 | -0.00 / %56.1 | -0.00 / %56.3 |
| 5 dk | -0.00 / %55.7 | -0.00 / %55.0 | -0.00 / %55.4 | -0.00 / %54.7 | -0.00 / %54.3 | -0.00 / %54.5 |
| 15 dk | -0.00 / %58.7 | 0.29 / %57.4 | 0.27 / %57.8 | 0.64 / %58.5 | 0.26 / %59.3 | 1.34 / %59.3 |
| 30 dk | 0.63 / %55.7 | 0.46 / %55.0 | 0.64 / %56.1 | 1.84 / %56.8 | 1.47 / %57.3 | 2.85 / %56.8 |
| 1 saat | 2.25 / %56.4 | 3.02 / %58.0 | 4.18 / %57.0 | 2.29 / %55.8 | 3.95 / %59.3 | 3.08 / %58.6 |
| 4 saat | 5.14 / %58.2 | 4.15 / %56.0 | 4.66 / %59.0 | 4.61 / %57.0 | 3.81 / %54.5 | 4.05 / %55.6 |
| 1 gün | 2.78 / %53.7 | 2.03 / %53.5 | 1.79 / %52.4 | 3.45 / %55.8 | 3.19 / %54.4 | 2.27 / %56.4 |
| 1 hafta | 0.17 / %56.2 | 0.28 / %48.7 | 1.94 / %57.0 | 1.39 / %55.3 | 2.48 / %57.9 | 2.48 / %57.9 |

## Hangi girdiler gerçekten işe yarıyor?

Seyrek (top-20) lojistik modelin 25 ayrı eğitiminde (8 TF × ufuklar) en sık seçilen girdiler ve LightGBM kazanç (gain) payı:

| Girdi | Grup | Top-20'de seçilme | LightGBM kazanç payı (toplam) |
|---|---|---|---|
| `wt` | momentum/volatilite | 22/25 | 0.52 |
| `rsi7` | momentum/volatilite | 21/25 | 1.25 |
| `ret3_atr` | mum | 20/25 | 0.38 |
| `rsi_slope3` | momentum/volatilite | 20/25 | 0.09 |
| `hist_slope` | momentum/volatilite | 20/25 | 0.22 |
| `rsi21` | momentum/volatilite | 19/25 | 0.67 |
| `rsi_z` | momentum/volatilite | 17/25 | 0.27 |
| `htf_rsi` | üst TF | 16/25 | 0.40 |
| `chan100` | mum | 16/25 | 0.17 |
| `c_ret_vol20` | mum-metrik korelasyon | 15/25 | 0.16 |
| `cci` | momentum/volatilite | 14/25 | 0.28 |
| `ema50_slope` | momentum/volatilite | 13/25 | 0.14 |
| `rsi` | momentum/volatilite | 12/25 | 1.01 |
| `htf_ema50_dist` | üst TF | 12/25 | 0.24 |
| `ema50_dist` | momentum/volatilite | 11/25 | 0.32 |
| `z_c_ret_vol20` | korelasyonun korelasyonu | 11/25 | 0.02 |
| `chan50` | mum | 10/25 | 0.08 |
| `m_rsi_vol20` | metrik-metrik korelasyon | 10/25 | 0.69 |
| `ret10_atr` | mum | 9/25 | 0.27 |
| `ema200_dist` | momentum/volatilite | 9/25 | 0.33 |

Grup bazında top-20 koltuk payı: momentum/volatilite **%47**, mum **%15**, mum-metrik korelasyon **%14**, üst TF **%8**, metrik-metrik korelasyon **%7**, korelasyonun korelasyonu **%5**, hacim **%2**, uyumsuzluk **%2**, zaman **%1**

## Kazanan yöntemin en etkili girdileri (Kural/konfluans araması (RSI-MACD-divergence-hacim-korelasyon))

- **5 dk** — AL: `di <= -0.458 & atr_pct >= 0.72 & htf_hist_atr <= -0.17` · SAT: — (kârlı SAT kuralı bulunamadı)
- **15 dk** — AL: `hist_atr <= -0.166 & atr_pct >= 1.62 & cmf20 <= -0.156` · SAT: — (kârlı SAT kuralı bulunamadı)
- **30 dk** — AL: `ret5_atr <= -1.58 & hist_atr <= -0.248 & atr_pct >= 2.3 VEYA rsi_z <= -1.97 & hist_atr <= -0.248 & atr_pct >= 2.3 VEYA rsi7 <= -0.519 & hist_atr <= -0.248 & atr_pct >= 2.3` · SAT: — (kârlı SAT kuralı bulunamadı)
- **1 saat** — AL: `ret3_atr <= -0.765 & hist_atr <= -0.164 & atr_pct >= 3.21` · SAT: — (kârlı SAT kuralı bulunamadı)
- **4 saat** — AL: `ret5_atr <= -1.01 & atr_pct >= 4.03 & atr_pct >= 5.1 VEYA ret5_atr <= -1.01 & atr_pct >= 5.1 VEYA rsi7 <= -0.283 & atr_pct >= 5.1 & cci <= -0.97` · SAT: `hist_slope3 >= 0.164 & atr_ratio >= 1.19 & divc14 <= -0.655 VEYA atr_ratio >= 1.19 & wt_diff >= 1.17 & divc14 <= -0.655 VEYA hist_slope3 >= 0.108 & atr_ratio >= 1.19 & divc14 <= -0.655`
- **1 gün** — AL: `c_close_hist50 >= 0.738` · SAT: `hist_atr >= 0.192 & c_close_hist20 <= -0.201 & c_close_hist50 <= 0.0899`
- **1 hafta** — AL: `div_hist_bull > 0.5 & c_close_rsi20 >= 0.982 & c_close_hist10 <= 0.454 VEYA c_close_hist50 >= 0.739 & m_rsi_mfi10 <= 0.293 & m_rsi_mfi20 <= 0.469 VEYA div_hist_bull > 0.5 & c_close_hist10 <= 0.454` · SAT: `ret3_atr <= -1.06 & streak <= -0.8 & m_rsi_vol20 <= -0.125 VEYA ret3_atr <= -1.06 & streak <= -0.8 & rsi21 <= -0.156 VEYA ret5_atr <= -1.41 & streak <= -0.8 & c_ret_vol20 <= -0.101`


### Kurallarda geçen girdilerin anlamı

| Girdi | Anlamı |
|---|---|
| `di` | (+DI − −DI) / 50 — yön gücü |
| `atr_pct` | ATR(14) / fiyat × 100 — volatilite (%) |
| `htf_hist_atr` | Üst zaman diliminin (kapanmış mum) MACD histogramı / ATR |
| `hist_atr` | MACD histogramı / ATR — normalize momentum |
| `cmf20` | Chaikin Money Flow (20) — hacim ağırlıklı alım/satım baskısı |
| `ret5_atr` | Son 5 mumda fiyat değişimi / ATR |
| `rsi_z` | RSI(14)'ün 50 mumluk z-skoru |
| `rsi7` | (RSI(7) − 50) / 50 |
| `ret3_atr` | Son 3 mumda fiyat değişimi / ATR |
| `cci` | CCI(20) / 100 |
| `hist_slope3` | MACD histogramının 3 mumluk değişimi / ATR |
| `atr_ratio` | ATR(5) / ATR(50) — volatilite genişlemesi |
| `divc14` | Sürekli uyumsuzluk: fiyat eğimi − RSI eğimi (14 mum) |
| `wt_diff` | WaveTrend hızı (wt1 − wt2) / 10 |
| `c_close_hist50` | corr(kapanış, MACD-hist) 50 mum — mum-metrik korelasyon |
| `c_close_hist20` | corr(kapanış, MACD-hist) 20 mum — mum-metrik korelasyon |
| `div_hist_bull` | MACD-histogram pozitif (bullish) uyumsuzluk, son 10 mumda (azalan ağırlık) |
| `c_close_rsi20` | corr(kapanış, RSI) 20 mum — mum-metrik korelasyon |
| `c_close_hist10` | corr(kapanış, MACD-hist) 10 mum — mum-metrik korelasyon |
| `m_rsi_mfi10` | corr(RSI, MFI) 10 mum — metrik-metrik korelasyon |
| `m_rsi_mfi20` | corr(RSI, MFI) 20 mum — metrik-metrik korelasyon |
| `streak` | Ardışık yükselen(+)/düşen(−) mum sayısı / 5 |
| `m_rsi_vol20` | corr(RSI, hacim) 20 mum — metrik-metrik korelasyon |
| `rsi21` | (RSI(21) − 50) / 50 |
| `c_ret_vol20` | corr(getiri, hacim) 20 mum — mum-metrik korelasyon |

**Yorum:** kısa ve orta vadeli zaman dilimlerinde (5 dk – 4 saat) bulunan AL kuralları aynı piyasa davranışını yakalıyor: *volatilite yüksekken (ATR/fiyat eşiğin üstünde) momentumun sert negatife dönmesi (MACD-hist/ATR çok düşük, son 3–5 mumda ATR cinsinden sert düşüş, RSI aşırı düşük)* → kısa süreli tepki yükselişi. 1 gün ve 1 hafta kurallarında ise **mum-metrik ve metrik-metrik korelasyonlar** (kapanış–MACD, kapanış–RSI, RSI–MFI, RSI–hacim) ve MACD uyumsuzluğu belirleyici.

## Sağlamlık: kâr tek bir güne mi bağlı?

Kripto çöküş günlerinde (ör. 10 Ekim 2025) çok sayıda coin aynı anda sinyal verir. Aşağıda en kârlı tek gün çıkarıldığında sonuçlar:

| TF | Coin grubu | İşlem | Medyan net/işlem | Ort. net/işlem | En iyi gün | O günün toplam kâra payı | En iyi gün hariç: yön isabeti | En iyi gün hariç: net/işlem |
|---|---|---|---|---|---|---|---|---|
| 5 dk | Eğitim | 183 | %0.44 | %0.46 | 2026-06-04 | %53 | %59.5 | %0.24 |
| 5 dk | Görülmemiş | 121 | %0.24 | %0.31 | 2026-06-04 | %52 | %59.8 | %0.15 |
| 15 dk | Eğitim | 127 | %-0.86 | %-0.92 | 2026-06-06 | - | %40.8 | %-1.03 |
| 15 dk | Görülmemiş | 111 | %-0.10 | %0.24 | 2026-06-04 | %113 | %49.5 | %-0.03 |
| 30 dk | Eğitim | 137 | %0.32 | %2.95 | 2025-10-10 | %98 | %50.0 | %0.08 |
| 30 dk | Görülmemiş | 103 | %0.43 | %0.98 | 2025-10-10 | %59 | %59.3 | %0.45 |
| 1 saat | Eğitim | 92 | %0.45 | %0.39 | 2025-10-10 | %123 | %52.0 | %-0.11 |
| 1 saat | Görülmemiş | 89 | %0.34 | %0.78 | 2026-06-03 | %20 | %58.0 | %0.63 |
| 4 saat | Eğitim | 250 | %0.14 | %0.50 | 2025-10-11 | %57 | %51.0 | %0.23 |
| 4 saat | Görülmemiş | 118 | %0.89 | %1.20 | 2026-02-05 | %41 | %57.1 | %0.74 |
| 1 gün | Eğitim | 235 | %-0.65 | %-0.93 | 2026-09-09 | - | %47.2 | %-1.30 |
| 1 gün | Görülmemiş | 65 | %-3.84 | %-0.80 | 2026-05-29 | - | %46.9 | %-1.93 |
| 1 hafta | Eğitim | 58 | %1.63 | %1.18 | 2026-08-24 | %71 | %64.9 | %0.35 |
| 1 hafta | Görülmemiş | 20 | %-2.75 | %3.99 | 2026-08-10 | %88 | %42.1 | %0.49 |

**Okuma:** 30 dk ve 1 saatteki yüksek ortalama kâr büyük ölçüde 10 Ekim 2025 çöküşünün ardından gelen tepki alımlarından geliyor; o gün çıkarıldığında ortalama net kâr sıfıra yaklaşıyor, fakat medyan işlem pozitif kalıyor. Bu yöntem 'panik satışlarında tepki alımı' mantığıyla çalışır: sakin piyasada az sinyal verir, sert çöküşlerde güçlü çalışır. 5 dk ve 4 saat sonuçları daha dağınık günlere yayılmıştır.

## Sızıntı kontrolü (karıştırılmış etiket testi)

Aynı lojistik hat, doğrulama döneminde bir kez gerçek etiketlerle bir kez de coin içinde karıştırılmış etiketlerle eğitildi. Sızıntı olsaydı karıştırılmış model de 'başarılı' görünürdü.

| Test | Gerçek etiket yön isabeti | Karıştırılmış etiket yön isabeti | Rastgele sinyal |
|---|---|---|---|
| 1h H12 | %59.3 | %48.8 | %50.0 |
| 4h H6 | %59.7 | %51.7 | %48.6 |

## Çoklu deneme düzeltmesi (Deflated Sharpe)

18 bin denemeden 'en iyisini seçmenin' şans payı düşülerek, kilitli testteki performansın gerçek bir avantaj olma olasılığı (Bailey & López de Prado):

| TF | Eğitim coinleri | Görülmemiş coinler |
|---|---|---|
| 5 dk | %22 | %15 |
| 15 dk | %0 | %3 |
| 30 dk | %89 | %31 |
| 1 saat | %28 | %56 |
| 4 saat | %20 | %53 |
| 1 gün | %0 | %1 |
| 1 hafta | %1 | %16 |

Not: Kilitli test ~13–15 aylık tek bir piyasa dönemidir; TF başına 60–250 işlem olduğu için güven aralıkları geniştir. Geçmiş performans geleceği garanti etmez, yatırım tavsiyesi değildir.

**Pine paritesi:** dışa aktarılan Pine parametreleriyle yeniden hesaplanan işlemler, araştırma modelinin kilitli testteki işlemleriyle 7 TF'nin 7 tanesinde birebir aynı (ortalama örtüşme %100.0).
