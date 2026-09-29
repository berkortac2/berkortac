# İnternet Araştırması — Özet ve Projeye Etkisi

Bu dosya, sinyal motorunu tasarlamadan önce yapılan araştırmanın özetidir. Her
bulgunun altında projede **nasıl kullanıldığı** yazılıdır.

## 1. Başarılı TradingView ML örneği: "Machine Learning: Lorentzian Classification" (jdehorty)
- TradingView'ın 2023 "Most Valuable" Pine yayını. Geçmiş mumlardan çok boyutlu bir özellik uzayında
  **k-En Yakın Komşu (kNN)** araması yapar; mesafe olarak Öklid yerine **Lorentzian mesafe**
  `Σ log(1+|xᵢ−yᵢ|)` kullanır (aykırı değerlere ve piyasa "çarpılmalarına" dayanıklı).
- Varsayılanlar: 5 özellik (RSI, WaveTrend, CCI, ADX, RSI), k = 8 komşu, 2000 mum geriye bakış,
  her 4 mumda bir örnekleme, 4 mum ileri etiket; volatilite / rejim / ADX / EMA filtreleri ve
  Nadaraya-Watson kernel regresyonu ile teyit.
- **Projede:** `src/tsa/models/online.py::lorentzian_knn` — aynı mesafe, ama tam (approximate değil)
  komşu araması, etiketin olgunlaşma gecikmesi (j ≤ t−H) ile. 4 farklı özellik seti denendi:
  klasik LDC seti, momentum+hacim, **korelasyon seti** ve karma set.

## 2. López de Prado — Advances in Financial Machine Learning
- **Triple-barrier etiketleme:** üst (TP), alt (SL) ve zaman bariyeri; gerçek bir pozisyonun
  nasıl kapandığını taklit eder.
- **Meta-labeling:** birincil model yönü verir, ikincil model "bu sinyali al / alma" der — isabeti artırır.
- **Purged (temizlenmiş) walk-forward + embargo:** etiket penceresi test dönemine taşan eğitim
  örnekleri atılır, sınıra tampon bölge konur → sızıntı (leakage) engellenir.
- **Deflated Sharpe Ratio:** denenen strateji sayısı arttıkça "en iyi" sonucun şans eseri çıkma
  ihtimalini düzeltir.
- **Projede:** `labels.py` (ATR tabanlı TP/SL + zaman bariyeri, aynı mumda ikisi birden → önce SL),
  `search/runner.py` (purged walk-forward, 50 mum embargo, `meta` ailesi), `eval/metrics.py`
  (Deflated Sharpe olasılığı, Wilson güven aralığı).

## 3. Maliyet gerçeği: saatlik BTC futures ML çalışması (2026, arXiv 2606.00060)
- Binance BTC/USDT futures, saatlik, 2018–2026, XGBoost / LSTM / iTransformer, 27 katlı walk-forward.
- Maliyetsiz XGBoost long-only yıllık +%73.5 → 10 bp maliyetle **−%64**. Sadece tahmin büyüklüğü
  maliyet eşiğini geçtiğinde işlem açan filtre ile işlem sayısı 10.619 → 251'e indi ve getiri tekrar
  pozitife döndü (+%65, Sharpe > 1).
- Sonuç: kripto kısa vadede "zayıf ama var olan" öngörülebilirlik; asıl sorun **tahminin işleme
  dönüştürülmesi** ve işlem sıklığı.
- **Projede:** tüm skorlar komisyon (%0.05/taraf) + kayma (%0.02/taraf) **sonrası** ölçülür; sinyal
  eşiği skor dağılımının üst %0.5–%10'luk dilimlerinden seçilir (maliyet-farkında filtre).

## 4. Mum formasyonları
- 23 kripto × 68 formasyon incelemesi: formasyonların tek başına **işe yaramadığı** sonucu.
- 400 kripto, saatlik, 55 dönüş formasyonu (SPA testi ile data-snooping düzeltmeli): Bullish Harami,
  Hikkake gibi bazı formasyonlar istatistiksel olarak anlamlı ama küçük etki.
- **Projede:** formasyonlar ayrı sinyal değil, modele giren **özellik** (gövde/fitil oranları,
  engulfing, pin bar, ardışık mum sayısı, kanal içi konum).

## 5. RSI / MACD / uyumsuzluk (divergence)
- 10 kripto üzerinde RSI vs MACD karşılaştırması: RSI sinyali daha isabetli, MACD trend dönüşlerini
  yakalamada daha esnek.
- Divergence için bağımsız backtestlerde isabet ~%50–65 (teyitle), teyitsiz belirgin düşüş; hakemli
  bir çalışma yok → sayılar yön gösterici. Yüksek zaman dilimlerinde daha iyi çalıştığı belirtiliyor.
- Hacim ile 7 günlük RSI (r≈0.45) ve ATR (r≈0.48) arasında anlamlı pozitif korelasyon; hacim-volatilite
  korelasyonu 0.75–0.95.
- **Projede:** TradingView'ın yerleşik "RSI Divergence Indicator" mantığı birebir (pivot sol=5, sağ=3/5,
  aralık 5–60 mum) RSI ve MACD-histogram için regular + hidden, bull + bear; pivot **sağ mum sayısı
  kadar gecikmeyle** onaylanır (repaint yok). Ayrıca sürekli uyumsuzluk ölçüsü (fiyat eğimi − RSI eğimi).

## 6. Korelasyon özellikleri
- Hacim-fiyat: yükselişte hacim artışı = devam, yükselişte hacim düşüşü = momentum kaybı / dönüş.
- Kriptolar arası korelasyonlar stres dönemlerinde artıyor (rejim değişimi göstergesi).
- **Projede (kullanıcının istediği katmanlar):**
  - Mum-metrik: corr(getiri, hacim), corr(|getiri|, hacim), corr(gövde, hacim), corr(kapanış, RSI),
    corr(kapanış, OBV), corr(kapanış, hacim), corr(aralık, hacim), corr(kapanış, MACD-hist) — 10/20/50 mum.
  - Metrik-metrik: corr(RSI, MACD-hist), corr(RSI, MFI), corr(RSI, hacim).
  - Korelasyonun korelasyonu: corr(corr(getiri,hacim), corr(kapanış,RSI)), corr(corr(kapanış,OBV),
    corr(kapanış,RSI)), corr(corr(gövde,hacim), corr(RSI,MFI)), corr(corr(kapanış,OBV), kapanış),
    corr(corr(gövde,hacim), kapanış) — 20/50 mum; ayrıca 5 mumluk korelasyon değişimi ve
    100 mumluk korelasyon z-skoru (korelasyon rejimi kırılması).

## 7. Pine Script v6 kısıtları (resmi dokümantasyon)
- Üst zaman dilimi verisi için tek önerilen repaint'siz kalıp:
  `request.security(sym, tf, expr[1], lookahead = barmerge.lookahead_on)`.
- Alt zaman dilimi için `request.security_lower_tf()` (dizi döner, ≤200.000 intrabar).
- En fazla 40 benzersiz `request.*()` çağrısı (Ultimate planda 64); 64 plot; betik çalışma süresi
  20 sn (ücretsiz) / 40 sn.
- TradingView'da Binance perpetual OI verisi `BINANCE:<COIN>USDT.P_OI` sembolüyle alınabiliyor.
  (Veri tarafında data.binance.vision OI geçmişi kısa olduğundan bu sürümde modele eklenmedi.)
- **Projede:** MTF tablo repaint'siz kalıpla, sinyaller sadece `barstate.isconfirmed` mumlarda.

## 8. Gerçekçi beklenti
- "%86–87 yön isabeti" gibi iddialar genelde tek varlık, sızıntılı etiket, repaint veya maliyetsiz
  değerlendirmeden geliyor. Walk-forward + maliyet dahil çalışmalarda kısa vadeli isabet tipik olarak
  %52–60 bandında. Bu projede her yüzde; kilitli test dönemi, görülmemiş coinler, rastgele sinyal
  baz çizgisi ve güven aralığıyla birlikte raporlanır.

## Kaynaklar
- [Machine Learning: Lorentzian Classification — jdehorty (TradingView)](https://www.tradingview.com/script/WhBzgfDu-Machine-Learning-Lorentzian-Classification/)
- [ML: Lorentzian Classification Premium — jdehorty](https://www.tradingview.com/script/Ts0sn9jl-ML-Lorentzian-Classification-Premium/)
- [advanced-ta (PyPI) — Lorentzian sınıflandırıcının Python portu](https://pypi.org/project/advanced-ta/)
- [Machine Learning-Based Bitcoin Trading Under Transaction Costs: Evidence From Walk-Forward Forecasting (arXiv 2606.00060)](https://arxiv.org/html/2606.00060v1)
- [The Quarter-Hour Effect: Periodic Algorithmic Trading and Return Predictability in Cryptocurrency Futures (arXiv 2607.09426)](https://arxiv.org/pdf/2607.09426)
- [Intraday return predictability in the cryptocurrency markets: Momentum, reversal, or both (ScienceDirect)](https://www.sciencedirect.com/science/article/abs/pii/S1062940822000833)
- [Evaluating machine learning models for predictive accuracy in cryptocurrency price forecasting (PMC)](https://pmc.ncbi.nlm.nih.gov/articles/PMC12571449/)
- [A comparative study between RSI and MACD in cryptocurrency market 2020–2022 (ResearchGate)](https://www.researchgate.net/publication/377921778_a-comparative-study-between-rsi-and-macd-to-predict-opportunities-in-cryptocurrency-market-from-2020-to-2022_1)
- [Do Candlestick Patterns Work in Cryptocurrency Trading? (ResearchGate)](https://www.researchgate.net/publication/355991813_Do_Candlestick_Patterns_Work_in_Cryptocurrency_Trading)
- [Intraday price forecasts using candlestick patterns in cryptocurrency markets (ScienceDirect)](https://www.sciencedirect.com/science/article/pii/S1059056026002716)
- [Momentum Divergence: How to Spot One and Whether It Signals a Reversal (Botsfolio)](https://botsfolio.com/learn/divergence)
- [RSI Divergence: 4 Types, Crypto Examples (Plisio)](https://plisio.net/education/rsi-divergence-bullish-bearish)
- [Purged cross-validation (Wikipedia)](https://en.wikipedia.org/wiki/Purged_cross-validation)
- [Triple-Barrier Labeling, Explained (Quant Memo)](https://www.quantmemo.com/concepts/triple-barrier-labeling)
- [financial-ml-toolkit — AFML araç zinciri (GitHub)](https://github.com/younis-y/financial-ml-toolkit)
- [Pine Script Docs — Repainting](https://www.tradingview.com/pine-script-docs/concepts/repainting/)
- [Pine Script Docs — Other timeframes and data](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/)
- [Pine Script Limits (Chartrades)](https://chartrades.com/guides/tradingview-pine-script-limits/)
- [BTCUSDT.P_OI — TradingView](https://www.tradingview.com/symbols/BTCUSDT.P_OI/)
