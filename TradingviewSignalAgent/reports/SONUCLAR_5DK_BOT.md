# 5 Dakika — Derin Araştırma (2. tur), Çoklu Coin Tarayıcı ve Bot

Kısa sonuç: **Daha uzun geçmişle (2017–2026) yapılan 2. tur arama, kilitli testte ilk turun 5 dk modelini (v1)
geçemedi; v1 kullanılmaya devam ediyor.** 5 dakikada komisyonu karşılayan bir **SAT (short) kuralı yine bulunamadı.**
Bot, v1 kurallarını %8 borsa-tarafı acil stop ile çalıştırır; bu stop 2025 çöküş döneminde sonucu zarardan kâra çevirdi.

Tüm yüzdeler: Binance USDT-M futures, taker komisyon %0.05 + kayma %0.02 **her taraf** (gidiş-dönüş %0.14) düşülmüş.

## 1. Ne yapıldı

| | |
|---|---|
| Veri | USDT-M perpetual 5 dk, **2020-01 → 2026-09**, 12 coin (BTC, ETH, SOL, XRP, DOGE, BNB, NEAR, UNI, ADA, LINK, AVAX, BCH) + **spot 2017-08 → 2019-12** (2018 ayı piyasası; BTC, ETH, BNB, XRP, ADA, LTC; sadece eğitimde, ayrı segment) = 8,6 milyon mum |
| Yeni test coinleri | Hiçbir aşamada kullanılmamış 10 perp: ONDO, INJ, FET, APT, ETC, OP, CRV, TIA, ICP, WIF |
| Doğrulama | 4 katlı walk-forward (2021-01 → 2026-02), her kat yalnızca kendinden önceki veriyle eğitildi (50 mum embargo) |
| Denenenler | AL ve SAT **ayrı ayrı** kural araması (derinlik 3/4, ışın 40, 10'lu eşik ızgarası, 2 destek seviyesi), meta (kural + lojistik onayı), lojistik, GBM; ufuk 6/12/24/48 mum; 10 çıkış tipi (TP/SL/sadece-stop/süre) → **2.000 yeni deneme** (5 dk toplam 3.000+, kural aramasının içinde milyonlarca koşul kombinasyonu) |
| Kilitli test | 2026-02-16 → 2026-09-28 (5 dk'nın kilitli dönemi; ikinci kez kullanıldı, bu yüzden hiç kullanılmamış 10 coin ayrıca raporlandı) |
| Seçim | Kilitli teste bakmadan önce yazılı kural: "hassas" = en iyi skor, "sık" = skor ≥ 5 ve 4 katın 4'ünde pozitif olanlar içinde coin-gün başına en çok net kâr; yeni model ancak v1'i hem arama coinlerinde hem hiç kullanılmamış coinlerde geçerse Pine/bota gider |

## 2. Doğrulama dönemi (walk-forward, 2021–2026): sıklık ↔ isabet

| Sinyal sıklığı (coin başına/gün) | Net/işlem | Yön isabeti | Kazanma | Not |
|---|---|---|---|---|
| ~0.15 | **+%0.56** | %61.7 | %59.0 | "hassas" (H=6, süre çıkışı) |
| ~0.27 | +%0.44 | %65.6 | %51.2 | H=6, SL 1.5×ATR (coin-gün başına en yüksek toplam) |
| ~0.49 | +%0.17 | %65.1 | %40.7 | H=12, TP 2 / SL 1 ×ATR |
| ~0.86 | +%0.04 | %61.1 | %40.6 | komisyon kârı neredeyse yiyor |

SAT: 4 ufkun hiçbirinde eğitim döneminde komisyon sonrası pozitif kural çıkmadı (H=48'de bulunanlar doğrulamada zararda).
Lojistik / GBM en iyi hâlde +%0.08/işlem ile kural aramasının çok gerisinde kaldı.

## 3. Kilitli test (Şubat–Eylül 2026)

Net/işlem (işlem sayısı):

| Grup | Hassas (yeni) | Sık (yeni) | **v1 (ilk tur)** |
|---|---|---|---|
| Arama coinleri (12) | −%0.11 (71) | +%0.08 (479) | **+%0.39 (111)** |
| Diğer eğitim coinleri (8) | +%0.47 (70) | +%0.13 (375) | **+%0.56 (72)** |
| Görülmemiş coinler (6) | +%0.17 (197) | +%0.15 (777) | **+%0.31 (121)** |
| Hiç kullanılmamış coinler (10) | +%0.28 (180) | −%0.06 (1170) | **+%0.43 (185)** |
| Hiç kullanılmamış 10 coin, 2025-01 → 2026-02 | −%0.05 (702) | +%0.02 (3625) | −%0.11 (707) |

v1 yön isabeti %62.2 / %59.7 / %61.2 / %56.8, işlem kazanma %58.6 / %58.3 / %57.0 / %55.1.
**Karar: v1 kalıyor** (yeni modeller ön-kayıtlı koşulu sağlamadı). Doğrulamadaki güçlü sonuçlar büyük ölçüde
2021–2022'nin yüksek oynaklıklı dönemlerinden geliyordu; 2026'da aynı güç görülmedi.

## 4. v1'in güvenilirliği — dürüst tablo

- 36 coinde kilitli dönemde 489 işlem, işlem başı +%0.41. Ama kârın büyük kısmı **2–5 Haziran 2026**'daki tek bir
  oynaklık olayından: en iyi gün hariç +%0.26, en iyi 3 gün hariç **+%0.10**.
- Aylık (işlem başı ort.): Şub −0.30, Mar +0.07, Nis +0.34, May −0.03, **Haz +1.34**, Tem +0.42, Ağu +0.30, Eyl −0.28 (%).
- Deflated Sharpe olasılığı 0.20–0.37: binlerce deneme düşünüldüğünde istatistiksel olarak "kesin" değil.
- Hiç kullanılmamış 10 coinde 2025 döneminde süre çıkışlı hâliyle **zararda** (−%0.11); en kötü işlem −%56.7
  (10 Ekim 2025 çöküşünde "düşen bıçağı" yakalama).

### Botun acil stop'u (%8, varsayılan — bu sonuçlardan önce konmuştu)

| Dönem | Sadece süre çıkışı | + %8 acil stop (bot) |
|---|---|---|
| Kilitli, 36 coin | +%0.41/işlem, en kötü −%6.5 | +%0.41/işlem, en kötü −%8.1 |
| 2025-01 → 2026-02, 10 coin | −%0.11/işlem, en kötü **−%56.7** | **+%0.74/işlem**, en kötü −%8.1 |

Not: gerçek bir çöküşte stop emri %8'den daha kötü bir fiyattan dolabilir (simülasyon, mum içi fitillerde stop
fiyatından dolum varsayar).

## 5. Bot simülasyonu (1.000 USDT bütçe, %8 acil stop, pozisyon başı marjin = bütçe / N)

| N pozisyon | Kaldıraç | Kilitli dönem (8 ay) | Maks. düşüş | 2025-01→2026-02 (14 ay, 10 coin) | Maks. düşüş |
|---|---|---|---|---|---|
| 2 | 1x | +%19.8 | %11.2 | −%2.0 | %47 |
| 4 | 1x | +%11.8 | %7.5 | +%7.2 | %29.6 |
| **8** | **1x** | **+%11.1** | **%4.2** | **+%5.2** | **%24.0** |
| 4 | 2x | +%23.6 | %14.2 | +%14.4 | %58 |

Bu yüzden botun varsayılanları **8 pozisyon, 1x kaldıraç, izole marjin, %8 acil stop** yapıldı.
2x ve üzeri kaldıraç, 2025 benzeri bir yılda %50'yi aşan düşüşler üretiyor.

## 6. Komisyon senaryoları (v1, kilitli test, net/işlem)

| Grup | Futures taker (%0.14) | Maker giriş (%0.09) | Maker her iki taraf (%0.04) | Spot taker, sadece AL (%0.24) |
|---|---|---|---|---|
| Arama coinleri | +%0.39 | +%0.44 | +%0.49 | +%0.29 |
| Hiç kullanılmamış coinler | +%0.43 | +%0.48 | +%0.53 | +%0.33 |

Spot her durumda daha pahalı; futures seçimi doğru.
**Limit (maker) giriş çalışması** (walk-forward, 3.295 sinyal): sinyal kapanışına post-only limit, 1 mum geçerli →
dolum oranı %98, işlem başı +%0.56 → **+%0.59**, 4 katın 4'ünde daha iyi. Botta henüz yok (kısmi dolum/iptal
mantığı gerçek parada ek risk getirdiği için); doğrulanmış bir sonraki adım olarak bırakıldı.

## 7. 5 dk çoklu coin tarayıcı (Pine)

`pine/TradingviewSignalAgent_Scanner5m.pine` — 36 Binance USDT-M perpetual coini tek göstergede tarar.
- Herhangi bir **5 dakikalık** grafiğe ekle (örn. BINANCE:BTCUSDT.P). Sağ üstteki tabloda hangi coinin AL verdiği,
  kaç mum önce ve sinyal fiyatı görünür; *Tabloda sadece aktif sinyalleri göster* ile liste kısalır.
- Alarm: *Create alert* → Condition: **TSA 5m Coin Tarayici** → **Any alert() function call** → her yeni kapanışta
  sinyal veren coinlerin adlarını tek mesajda gönderir (ör. `TSA 5m | AL: NEARUSDT, UNIUSDT`).
- Coin listesi ayarlardan değiştirilebilir (en fazla 39; TradingView'ın 40 istek sınırı). 1 saatlik bağlam, iç içe istek
  yerine 5 dk mumlardan birebir yeniden kurulur (araştırma özellikleriyle farkı 0, `tests/test_scanner.py`).
- Sinyal kapanmış mumda verilir, repaint yoktur.

## 8. Bot

Ayrıntılar: [`bot/README.md`](../bot/README.md). Binance USDT-M futures, yalnızca USDT pariteleri, bütçe limiti,
çekim/transfer yetkisi yok ve çekim izinli anahtarı reddeder, anahtar şifreli ve dışarı çıkmaz, modern yerel arayüz,
paper/testnet/canlı/replay modları. Testler: 47 güvenlik/birim + motor=backtest eşleşmesi + Chromium arayüz testi;
bağımsız güvenlik incelemesinin 10 bulgusu düzeltildi ve regresyon testine çevrildi.

## 9. AL pozisyonundan ne zaman çıkılır? (kâr al / ÇIK) — 3. tur

v1'in AL girişleri sabit tutularak **3.240 çıkış politikası** denendi: en uzun tutma (12/24/48/96 mum) × ATR kâr al
(yok/1/1.5/2/3/5) × ATR zarar kes (yok/1.5/3) × iz süren stop × gösterge çıkışı (RSI, EMA50, MACD histogram,
Stochastic, CCI, WaveTrend, kanal üstü, DI). Hepsinde %8 acil stop var. Seçim, AL kuralının **hiç görmediği 2017–2023**
verisi ile 2023–2026 döneminin en zayıf t-istatistiğine göre önceden belirlendi; kilitli test ve 2025 yalnızca kontrol.

**Kazanan (bot, Pine ve tarayıcıda artık varsayılan):**
- **ÇIK (kâr al): WaveTrend (wt1) ≥ 50** — fiyat tepki yükselişinde aşırı alım bölgesine gelince, mum kapanışında çık.
- **Zarar kes: giriş anındaki ATR'nin 3 katı** (veya %8 acil stop, hangisi yakınsa).
- **En geç 96 mum (8 saat)** sonra çık.

| Dönem | Eski çıkış (24 mum + %8 stop): işlem · net/işlem · kazanma | Yeni ÇIK politikası: işlem · net/işlem · kazanma · ort. süre | Toplam kâr değişimi |
|---|---|---|---|
| 2017–2023 (AL kuralı hiç görmedi) | 4.575 · +%0.54 · %58.8 | 5.450 · +%0.48 · %54.0 · 27 mum | **+%5** |
| 2023-09 → 2026-02 | 1.317 · +%0.45 · %59.9 | 1.537 · +%0.59 · %58.5 · 28 mum | **+%51** |
| Kilitli test 2026 (36 coin) | 489 · +%0.41 · %56.9 | 543 · +%0.42 · **%63.0** · 35 mum | **+%16** |
| Hiç kullanılmamış 10 coin, 2025 | 760 · +%0.74 · %51.6 | 884 · +%0.69 · %51.7 · 30 mum | **+%8** |

- İşlemlerin %54–69'u ÇIK sinyaliyle, %29–44'ü stop ile kapanıyor. Pozisyon erken kapandığı için coin daha çabuk
  serbest kalıyor ve biraz daha çok işlem açılıyor; coin-gün başına toplam kâr **dört dönemin dördünde** artıyor
  (3.240 politikadan bunu başaran 16 politikadan biri).
- Aynı AL girişleri 2017–2023'te (hiç görülmemiş veri) de işlem başı +%0.5 kazandırıyor: giriş kuralı da
  bağımsız veride doğrulanmış oldu.
- Bölüm 5'teki portföy simülasyonu eski çıkışla yapılmıştı; yeni çıkış her dönemde toplam kârı artırdığı için o tablo
  ihtiyatlı kabul edilebilir.
- Motorun ÇIK mantığı araştırma simülatörüyle birebir test edildi (`bot/tests/test_engine_replay.py`).

## 10. SAT (short) araması — 3. tur

| Yaklaşım | Sonuç |
|---|---|
| Kural araması, hedef = süre çıkışlı short getirisi (2. tur, 4 ufuk) | Eğitimde pozitif kural yok; H=48'dekiler doğrulamada zararda |
| Kural araması, hedef = **TP/SL'li short sonucu** (TP/SL/H: 1/1/12, 1.5/1/12, 1/1.5/24, 2/2/48, 0.75/1.5/6; 2 destek seviyesi; 4 kat) | Hiçbir ayarda komisyon sonrası pozitif short kuralı çıkmadı |
| AL kuralının aynası ("oynaklıkta aşırı yükseliş → dönüş"), 108 çıkış ayarı | 2017–2023'te en iyi hâli ≈ 0 (+%0.008), 2023–2026, kilitli test ve 2025'te **zararda** (−%0.05 … −%0.19) |

**Sonuç:** 5 dakikada, %0.14 komisyon+kayma sonrası kazandıran bir SAT (short) girişi bulunamadı. Bu yüzden bot ve
5 dk tarayıcı **sadece AL** açar; "SAT" ihtiyacını karşılayan şey **ÇIK sinyali**: AL pozisyonunu ne zaman satıp kârı
alacağını söyler. Pine göstergesindeki SAT etiketleri yalnızca 4 saat ve 1 hafta grafiklerinde (1. tur) vardır ve
zayıftır (4s short: eğitim coinlerinde +%0.14, görülmemiş coinlerde −%0.26/işlem); short girişi yerine
"risk / kâr al uyarısı" olarak kullanılması önerilir.

## 11. Sonuç ve öneri

- 5 dk'da işe yarayan tek desen: **yüksek oynaklıkta sert düşüş sonrası kısa süreli tepki alımı (AL)**. Kârları olaylara
  bağlı; sakin aylarda küçük zararlar olur. SAT tarafı 5 dk'da komisyonu karşılamıyor.
- Önce **Paper** modunda en az 2–4 hafta çalıştır, sonuçları bu tabloyla karşılaştır; canlıda küçük bütçe ve 1x kaldıraçla başla.
- Yatırım tavsiyesi değildir; geçmiş performans geleceği garanti etmez.

Dosyalar: `reports/deep5m_final_results.csv`, `deep5m_models.json`, `deep5m_final_trades.parquet`,
`bot_trades_5m.parquet`, `portfolio_sim_5m.json`, `entry_study.json`, `search/trials_5m_deep.parquet`.
Betikler: `scripts/08`–`18` (çıkış: `17_exit_study.py`, SAT: `16_short_search.py`, `18_short_mirror.py`).
