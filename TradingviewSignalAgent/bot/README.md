# TSA Bot — Binance USDT-M Futures sinyal botu

Tradingview Signal Agent'ın **5 dakikalık** modelini (Pine göstergesindeki kuralların aynısı) Binance
**USDT-M perpetual futures** hesabında otomatik işleme çeviren, bilgisayarında çalışan bir uygulama.

- Sadece **USDT pariteleri** (`BTCUSDT`, `ETHUSDT` …): başka her sembol reddedilir.
- **AL = long, SAT = short**. Her coin'de aynı anda tek pozisyon.
- Senin verdiğin **bütçe (USDT)** kadar marjin kullanır, fazlasını asla kullanmaz.
- **Para çekme / transfer yapamaz**: kodda bu uç noktalara giden bir yol yok; çekim veya transfer izni
  açık bir API anahtarıyla canlı modda çalışmayı reddeder.
- API anahtarın bilgisayarından çıkmaz (yalnızca Binance'e imzalı istek için kullanılır).

## Neden futures?

Senin tercihin ve araştırmanın verisi futures (perpetual) olduğu için **futures** seçildi:
modeller `BINANCE:XXXUSDT.P` verisiyle eğitildi ve test edildi; SAT (short) sinyalleri ancak futures'ta
kazanca dönüşebilir. Spot'ta komisyon (%0.1/taraf) futures taker'dan (%0.05) **daha yüksek**,
dolayısıyla spot bu strateji için daha pahalı. Varsayılan kaldıraç **1x**, 8 eş zamanlı pozisyon ve **izole marjin**: bir pozisyonun
en kötü durumda kaybedebileceği para o pozisyona ayrılan marjinle sınırlı.

## Kurulum (Windows / macOS / Linux)

1. **Python 3.11+** kur (python.org). Windows'ta kurulumda "Add Python to PATH" kutusunu işaretle.
2. Bu depoyu indir: GitHub'da dal `claude/awesome-mccarthy-r3cum5` → **Code → Download ZIP** → klasöre çıkar.
3. Terminal / Komut İstemi'nde:
   ```bash
   cd berkortac/TradingviewSignalAgent/bot
   python -m pip install -r requirements.txt
   python -m tsabot
   ```
   (Windows'ta `python` yerine `py` yazman gerekebilir.)
4. Terminalde çıkan adresi aç: `http://127.0.0.1:8765/#setup=...` → **arayüz şifreni belirle**
   (en az 10 karakter). Bu şifre aynı zamanda API anahtarlarını şifreler.
5. Sonraki açılışlarda sadece `python -m tsabot` ve `http://127.0.0.1:8765`.

Arayüz yalnızca bu bilgisayardan (127.0.0.1) açılır. Uzak sunucuda çalıştıracaksan SSH tüneli kullan.

## Önerilen sıra: Replay → Paper → Testnet → Canlı

| Mod | Ne yapar | Anahtar gerekir mi |
|---|---|---|
| **Replay** | Kayıtlı geçmiş mumları hızlıca oynatır; arayüzü ve mantığı görmek için | Hayır (araştırma verisi gerekir) |
| **Paper** | Canlı Binance fiyatlarıyla **sanal** para; komisyon + kayma dahil | Hayır |
| **Testnet** | Binance demo futures hesabında gerçek emirler (sahte para) | Testnet anahtarı |
| **Canlı** | Gerçek para | Canlı anahtar + onay kutusu |

Paper modunda en az 1–2 hafta çalıştırıp sonuçları araştırma raporuyla karşılaştırman önerilir.

## Binance API anahtarı (canlı)

Binance → Profil → **API Management** → Create API (System generated):

1. Sadece **Enable Reading** ve **Enable Futures** açık olsun.
2. **Enable Withdrawals**, **Permits Universal Transfer**, **Enable Internal Transfer** KAPALI olsun.
   Açıksa bot "Canlı hesabı kontrol et" adımında bunu görür ve **çalışmayı reddeder**.
3. **Restrict access to trusted IPs only** ile bilgisayarının IP'sini ekle (önerilir).
4. Futures → Tercihler → **Position Mode: One-way** seçili olsun (Hedge mode reddedilir).
5. Arayüzde **API anahtarları** sekmesine key + secret + arayüz şifreni gir → *Şifreleyip kaydet* →
   *Canlı hesabı kontrol et*. Tüm satırlar yeşil olmalı.

Testnet anahtarı için Binance **Demo Trading** hesabında (binance.com → Demo Trading → API Management)
anahtar oluştur; bot `https://demo-fapi.binance.com` adresini kullanır. Eski
<https://testnet.binancefuture.com> anahtarların varsa botu `TSABOT_TESTNET_BASE=https://testnet.binancefuture.com`
ortam değişkeniyle başlat. Alternatif olarak
anahtarı ortam değişkeniyle de verebilirsin: `BINANCE_API_KEY`, `BINANCE_API_SECRET` (dosyaya yazılmaz).

## Bütçe ve risk ayarları

- **Bütçe (USDT)**: botun aynı anda kullanabileceği toplam marjin. Pozisyon başı marjin =
  bütçe / *eş zamanlı pozisyon*. Örnek: 200 USDT, 8 pozisyon, 1x → her işlem 25 USDT marjin, 25 USDT büyüklük.
- Zarar ettikçe kullanılabilir bütçe küçülür; kâr **bileşik** kutusu işaretli değilse tekrar kullanılmaz.
  Böylece toplam risk hiçbir zaman verdiğin bütçeyi aşmaz.
- **Günlük zarar limiti**: aşılınca o gün (UTC) yeni işlem açılmaz.
- **Maks. toplam zarar**: aşılınca bot kendini durdurur.
- **Acil stop**: modelin TP/SL'si olmayan (süre çıkışlı) işlemlerinde borsada bekleyen koruma emri
  (bot kapalıyken ani çöküşe karşı).
- **Acil kapat** düğmesi: botun açtığı tüm pozisyonları piyasa fiyatından kapatır ve durdurur.
- Bot, senin elle açtığın pozisyonların olduğu coinlere dokunmaz.

## Ne beklemeli (geçmiş simülasyon, 1.000 USDT, 8 pozisyon, 1x, %8 acil stop)

| Dönem | Sonuç | Maks. düşüş |
|---|---|---|
| Şubat–Eylül 2026 (kilitli test, 36 coin) | +%11.1 | %4.2 |
| Ocak 2025–Şubat 2026 (hiç kullanılmamış 10 coin) | +%5.2 | %24.0 |

Kârlar olaylara bağlı (sert düşüş sonrası tepki alımları); sakin aylarda küçük zararlar olur. 2x kaldıraçta aynı
dönemlerde düşüş %50'yi aştı. Ayrıntı: [`../reports/SONUCLAR_5DK_BOT.md`](../reports/SONUCLAR_5DK_BOT.md).

## Bot nasıl işlem yapar

1. Her **5 dakikalık mum kapanışında** (+3 sn) izlenen paritelerin son 1200 mumu ve 1 saatlik mumları alınır.
2. Araştırmadaki özellik kodunun **aynısı** ile göstergeler hesaplanır (RSI, MACD, ADX/DI, ATR,
   korelasyonlar, 1 saatlik bağlam …) ve model kuralları kontrol edilir.
3. Sinyal varsa ve risk kuralları izin veriyorsa **piyasa emriyle** pozisyon açılır; TP/SL borsaya
   `algoOrder` (koşullu emir) olarak girilir; süre dolunca (H mum) piyasa emriyle kapatılır.
4. Tüm işlemler, komisyonlar ve net K/Z `data/bot.db` içinde saklanır ve arayüzde görünür.

Replay testi, botun kayıtlı veride **araştırma backtest'iyle aynı işlemleri** açıp kapattığını doğrular
(`tests/test_engine_replay.py`).

## Güvenlik özeti

| Önlem | Nerede |
|---|---|
| Uç nokta izin listesi (withdraw/transfer/deposit/convert… erişilemez) | `tsabot/exchange/binance.py` |
| Canlı modda çekim/transfer izinli anahtarı reddetme | `api.py` → `permission_check` |
| Secret: şifreli kasa (scrypt + Fernet), dosya izinleri 0600, geri okunamaz | `tsabot/secrets.py` |
| Loglarda anahtar/secret/imza maskeleme | `RedactFilter` |
| Sadece 127.0.0.1, Host başlığı kontrolü (DNS rebinding) | `api.py` |
| Oturum çerezi HttpOnly + SameSite=Strict, CSRF başlığı, sadece JSON gövde, Origin kontrolü | `api.py` |
| Kaba kuvvet kilidi (5 hatalı denemede artan bekleme) | `api.py` |
| Sıkı CSP (inline script/eval yok), X-Frame-Options, nosniff, no-referrer | `api.py` |
| Arayüzde tüm veriler `textContent` ile basılır (XSS yok) | `web/app.js` |
| Dış kütüphane / CDN yok (tedarik zinciri riski yok) | `web/` |

## Testler

```bash
cd TradingviewSignalAgent/bot
python -m pytest -q                      # birim + güvenlik (penetrasyon) + motor + arayüz testleri
python -m playwright install chromium    # arayüz testi için bir kez (tarayıcı yoksa)
```

- `tests/test_security.py`: izin listesi, imza (Binance doküman örneği), secret'ın hiçbir yanıtta / dosyada /
  logda görünmemesi, host/origin/CSRF/içerik tipi/gövde boyutu, kaba kuvvet kilidi, statik dosya yol aşımı,
  çekim izinli anahtarın reddi, bütçe ve limitler.
- `tests/test_engine_replay.py`: motor = araştırma backtest'i; bütçe hiç aşılmıyor.
- `tests/test_ui.py`: gerçek Chromium ile kurulum → giriş → ayarlar → replay işlemleri → XSS denemesi →
  mobil görünüm → çıkış/giriş.

## Önemli uyarılar

- Geçmiş performans geleceği garanti etmez; model kilitli testte kârlı olsa da canlı piyasada zarar edebilir.
  Kaldıraçlı işlemler sermayenin tamamını kaybettirebilir. Yatırım tavsiyesi değildir.
- Bilgisayar kapalıysa / internet kesikse bot çalışmaz (borsadaki TP/SL/acil stop emirleri yine çalışır).
- Önce Paper ve Testnet'te dene; canlıda küçük bütçeyle başla.
