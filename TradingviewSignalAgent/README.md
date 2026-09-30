# Tradingview Signal Agent

Binance USDT-M **perpetual futures** grafiklerinde (TradingView'da `BINANCE:<COIN>USDT.P`) çalışan,
**1m · 5m · 15m · 30m · 1s · 4s · 1G · 1H** zaman dilimleri için **AL (long) / SAT (short)** sinyali üreten
Pine Script v6 göstergesi ve onu bulan Python araştırma motoru.

- Sonuçlar ve yüzdeler: [`reports/SONUCLAR.md`](reports/SONUCLAR.md)
- **5 dk derin araştırma (2. tur), 36 coinlik 5 dk tarayıcı ve bot sonuçları:** [`reports/SONUCLAR_5DK_BOT.md`](reports/SONUCLAR_5DK_BOT.md)
- **5 dk çoklu coin tarayıcı (Pine):** [`pine/TradingviewSignalAgent_Scanner5m.pine`](pine/TradingviewSignalAgent_Scanner5m.pine)
- **Binance USDT-M futures botu (arayüzlü):** [`bot/README.md`](bot/README.md)
- İnternet araştırması özeti ve kaynaklar: [`research/ARASTIRMA.md`](research/ARASTIRMA.md)
- TradingView kodu: [`pine/TradingviewSignalAgent.pine`](pine/TradingviewSignalAgent.pine) (gösterge) ve
  [`pine/TradingviewSignalAgent_Strategy.pine`](pine/TradingviewSignalAgent_Strategy.pine) (Strategy Tester sürümü)

## Sonuç özeti (kilitli test, komisyon + kayma dahil)

18.466 strateji denemesi, 6 yöntem ailesi. En iyi 3 yöntem: **1) Kural/konfluans araması**, 2) Meta-labeling
(kural + lojistik onayı), 3) Lojistik skor modeli. Pine'a giden kazanan: **Kural/konfluans araması**.

| TF | Yön isabeti | İşlem kazanma | Net/işlem | İşlem | Görülmemiş coin yön isabeti | Pine'da varsayılan |
|---|---|---|---|---|---|---|
| 1 dk | – | – | – | – | – | model yok (komisyon > hareket) |
| 5 dk | %61.2 | %58.5 | +%0.46 | 183 | %61.2 | açık (sadece AL) |
| 15 dk | %41.7 | %40.2 | −%0.92 | 127 | %50.5 | kapalı (testte zarar) |
| 30 dk | %54.0 | %54.0 | +%2.95* | 137 | %57.3 | açık (sadece AL) |
| 1 saat | %56.5 | %55.4 | +%0.39* | 92 | %58.4 | açık (sadece AL) |
| 4 saat | %52.8 | %50.8 | +%0.50 | 250 | %59.3 | açık (AL + SAT) |
| 1 gün | %47.7 | %47.2 | −%0.93 | 235 | %47.7 | kapalı (testte zarar) |
| 1 hafta | %65.5 | %63.8 | +%1.18 | 58 | %45.0 | açık (AL + SAT) |

\* 30 dk ve 1 saat kârının büyük kısmı 10 Ekim 2025 çöküşündeki tepki alımlarından geliyor; ayrıntı ve
"en iyi gün hariç" analizi [`reports/SONUCLAR.md`](reports/SONUCLAR.md) içinde. Karşılaştırma: rastgele sinyal
%48.9, klasik RSI 30/70 %51.1, MACD kesişimi %47.8 yön isabeti ve hepsi komisyon sonrası zararda.

Bulunan ana desen: **yüksek volatilitede (ATR/fiyat yüksek) momentumun sert negatife dönmesi → kısa süreli tepki
alımı**; 1G/1H kurallarında mum-metrik ve metrik-metrik korelasyonlar (kapanış–MACD, kapanış–RSI, RSI–MFI, RSI–hacim)
ve MACD uyumsuzluğu belirleyici. SAT kuralları yalnızca 4s, 1G ve 1H'de doğrulamadan geçebildi.

## 2. tur (5 dk) özeti

2017–2026 arası 8,6 milyon 5 dk mumla AL ve SAT ayrı ayrı 2.000 yeni deneme yapıldı. Doğrulamada çok güçlü görünen
yeni kurallar kilitli testte (Şubat–Eylül 2026) ilk turun 5 dk modelini geçemedi; önceden yazılmış seçim kuralı gereği
**5 dk modeli değişmedi**. 5 dk'da komisyonu karşılayan SAT kuralı yine yok. Bot bu modeli %8 acil stop, 8 pozisyon,
1x kaldıraçla çalıştırır. Ayrıntı ve dürüst risk tablosu: [`reports/SONUCLAR_5DK_BOT.md`](reports/SONUCLAR_5DK_BOT.md).

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
- *Sadece kilitli testte kârlı çıkan TF'lerde sinyal ver* (varsayılan açık): 15 dk ve 1 gün testte zarar ettiği
  için bu TF'lerde etiket basılmaz; kapatırsan her TF'de sinyal üretilir. Tabloda bu TF'lerin kazanç hücresi turuncu ve "!".
- *Grafikten düşük TF'leri de hesapla* (varsayılan kapalı): örn. 4 saatlik grafikte 5 dk / 15 dk / 30 dk / 1 saat
  satırlarını da doldurur; uzun geçmişli grafiklerde yükleme süresini artırır.
- 1 dk grafikte model yoktur (hiçbir yöntem komisyon sonrası kâr üretemedi); tabloda "model yok" yazar.

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
