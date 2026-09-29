# Tradingview Signal Agent

Binance USDT-M **perpetual futures** grafiklerinde (TradingView'da `BINANCE:<COIN>USDT.P`) çalışan,
**1m · 5m · 15m · 30m · 1s · 4s · 1G · 1H** zaman dilimleri için **AL (long) / SAT (short)** sinyali üreten
Pine Script v6 göstergesi ve onu bulan Python araştırma motoru.

- Sonuçlar ve yüzdeler: [`reports/SONUCLAR.md`](reports/SONUCLAR.md)
- İnternet araştırması özeti ve kaynaklar: [`research/ARASTIRMA.md`](research/ARASTIRMA.md)
- TradingView kodu: [`pine/TradingviewSignalAgent.pine`](pine/TradingviewSignalAgent.pine) (gösterge) ve
  [`pine/TradingviewSignalAgent_Strategy.pine`](pine/TradingviewSignalAgent_Strategy.pine) (Strategy Tester sürümü)

## TradingView'a kurulum

1. TradingView'da grafiği aç, sembolü Binance perpetual yap: örn. `BINANCE:BTCUSDT.P`.
2. Alttaki **Pine Editor** sekmesini aç → *Open → New blank indicator* → içeriği sil.
3. `pine/TradingviewSignalAgent.pine` dosyasının tamamını yapıştır → **Save** → **Add to chart**.
4. Zaman dilimini 1m / 5m / 15m / 30m / 1h / 4h / 1D / 1W'den birine al; gösterge o zaman diliminin
   kendi parametrelerini otomatik seçer.
5. Alarm: grafikte *Alert* → Condition: **Tradingview Signal Agent** → `TSA AL` / `TSA SAT`
   ya da "Any alert() function call" (JSON mesajı: sinyal, sembol, TF, fiyat, ATR, TP/SL çarpanı, tutma süresi).
6. Performansı TradingView içinde görmek için `pine/TradingviewSignalAgent_Strategy.pine` dosyasını
   ayrı bir *strategy* olarak ekle ve **Strategy Tester** sekmesine bak (komisyon %0.05 ayarlı).

### Grafikte ne görürsün
- **AL** (yeşil, mumun altında) / **SAT** (bordo, mumun üstünde) etiketleri — yalnızca **kapanmış** mumda
  (repaint yok). Aynı anda tek pozisyon: bir sinyalin işlemi (TP / SL / H mum süresi) bitmeden yeni sinyal çıkmaz.
- Kesikli yeşil/kırmızı çizgiler: ATR tabanlı TP ve SL seviyeleri (ayar: *TP / SL seviyelerini çiz*).
- Sağ üstte **çoklu zaman dilimi tablosu**: her TF için anlık yön (AL / SAT / –), model skoru,
  kilitli testteki yön isabeti % ve işlem kazanma %, en altta **bu grafikte** geçmişteki canlı isabet.
- *Eşik çarpanı* > 1 → daha az ama daha seçici sinyal; < 1 → daha sık sinyal.

## Nasıl bulundu (kısaca)

1. **Veri**: data.binance.vision'dan USDT-M perpetual mumları (1m–1d; 1W günlükten Pazartesi 00:00 UTC ile
   üretildi). Likiditeye göre 20 eğitim coini + eğitimde hiç kullanılmayan 6 coin.
2. **Özellikler (109 adet)** — hepsi Pine'da birebir hesaplanabilir ve nedensel:
   mum anatomisi, RSI/MACD/MFI/ADX/CCI/WaveTrend, hacim akışı, TradingView'ın RSI/MACD **divergence** mantığı,
   **mum-metrik** ve **metrik-metrik korelasyonları** (10/20/50 mum), **korelasyonun korelasyonu**,
   korelasyon değişimi ve z-skoru, saat/gün, üst zaman diliminin *kapanmış* mumu.
3. **Mum-mum replay**: t anındaki karar sadece t'ye kadarki mumları görür; bir mumun sonucu
   (etiketi) ancak H mum sonra "olgunlaşır" ve ancak o zaman öğrenmeye girer. Online modeller
   (Lorentzian kNN, online lojistik) her coini tek tek baştan sona oynatarak kendini eğitir;
   offline modeller purged walk-forward ile (etiketi test dönemine taşan örnekler atılarak, 50 mum embargo)
   yeniden eğitilir.
4. **Arama**: 6 yöntem ailesi × özellik setleri × ufuk H × TP/SL × sinyal eşiği → binlerce deneme;
   seçim komisyon + kayma sonrası net kârın t-istatistiği ve katlar arası istikrarla.
5. **Kilitli test**: her TF'nin son ~%20'lik zamanı aramada hiç kullanılmadı; en iyi 3 yöntem burada
   bir kez, dondurulmuş parametrelerle (Pine'a giden ayarların aynısı) ölçüldü.
6. **Sızıntı kontrolleri**: kesme (truncation) testi, gelecek-karıştırma testi, karıştırılmış etiket testi
   (`tests/`, `scripts/sanity_shuffled_labels.py`).

## Yeniden üretme

```bash
pip install -r requirements.txt
python scripts/01_download.py                 # evren seçimi + tüm TF'lerin indirilmesi
python scripts/02_build_features.py           # özellikler (parquet)
python scripts/03_search.py --tf 1h           # her TF için arama (1m 5m 15m 30m 1h 4h 1d 1w)
python scripts/04_final_test.py               # en iyi 3 yöntem -> kilitli test
python scripts/06_report.py                   # reports/SONUCLAR.md + kazanan
python scripts/05_export_pine.py              # pine/*.pine üretimi
python -m pytest -q                           # testler
```

## Klasörler

| Yol | İçerik |
|---|---|
| `src/tsa/data` | Binance USDT-M indirici, haftalık yeniden örnekleme |
| `src/tsa/indicators/pine_ta.py` | Pine `ta.*` fonksiyonlarının birebir Python karşılıkları |
| `src/tsa/features/registry.py` | 109 özellik (mum, metrik, divergence, korelasyon, korelasyonun korelasyonu) |
| `src/tsa/labels.py` | ATR TP/SL + zaman bariyeri, tek pozisyon simülasyonu |
| `src/tsa/models` | lojistik, LightGBM, kural araması, Lorentzian kNN, online lojistik |
| `src/tsa/search/runner.py` | walk-forward replay araması |
| `src/tsa/final.py` | kilitli test |
| `src/tsa/export` | Pine v6 üretici (özelliklerin Pine ikizleri) |
| `reports/` | arama denemeleri, doğrulama sıralaması, kilitli test sonuçları, SONUCLAR.md |

> Uyarı: Geçmiş performans geleceği garanti etmez; bu çalışma yatırım tavsiyesi değildir. Kaldıraçlı
> futures işlemlerinde risk yönetimi (pozisyon büyüklüğü, zarar-kes) kullanıcının sorumluluğundadır.
