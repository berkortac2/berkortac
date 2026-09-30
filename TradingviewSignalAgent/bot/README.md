# TSA Bot — Binance USDT-M Futures sinyal botu (masaüstü uygulaması)

Tradingview Signal Agent'ın **5 dakikalık** modelini (Pine göstergesindeki kuralların aynısı) Binance
**USDT-M perpetual futures** hesabında otomatik işleme çeviren, bilgisayarında çalışan bir **uygulama**.
Tarayıcı gerekmez: kendi penceresi ve saatin yanındaki sistem tepsisinde bir simgesi vardır.

- Sadece **USDT pariteleri** (`BTCUSDT`, `ETHUSDT` …): başka her sembol reddedilir.
- **AL = long, SAT = short**. Her coin'de aynı anda tek pozisyon.
- Bota verdiğin **bütçe** kadar marjin kullanır, fazlasını asla kullanmaz. Bütçeyi dolar olarak yazarak,
  kaydırıcıyla ya da futures bakiyenin yüzdesi (%10 / %25 / %50 / %75 / %100) olarak ayarlarsın.
- **Senin elle açtığın pozisyonlara ve emirlerine dokunmaz.**
- **Para çekme / transfer yapamaz**: kodda bu uç noktalara giden bir yol yok; çekim veya transfer izni
  açık bir API anahtarıyla canlı modda çalışmayı reddeder.
- API anahtarın ve Telegram token'ın bilgisayarından çıkmaz; şifreli kasada durur, hiçbir ekranda,
  logda veya Telegram mesajında görünmez.
- **Telegram**'dan rapor alır, işlem geçmişini görür, botu başlatıp durdurur, pozisyon kapatırsın.

## Kurulum (Windows)

### A) Hazır program (önerilen)

1. GitHub'da depoya gir → üstte **Actions** → soldan **TSA Bot Windows** → en üstteki (yeşil tikli) çalıştırma.
2. Sayfanın altındaki **Artifacts** bölümünden **TSABot-windows**'u indir ve zip'i aç.
3. Klasörü kalıcı bir yere kopyala (ör. `C:\TSABot`) ve **TSABot.exe**'ye çift tıkla.
   Windows "bilinmeyen yayıncı" uyarısı verirse: **Ek bilgi → Yine de çalıştır**.
4. İlk açılışta uygulama şifreni belirle (en az 10 karakter). Bu şifre API anahtarını ve Telegram
   bilgisini de şifreler.

Bu paketi GitHub her güncellemede kendi Windows bilgisayarında kurar, botun testlerini orada çalıştırır ve
hazır programı açıp kendi kendini test eder (`--selftest`). Test geçmezse paket yayınlanmaz.

### B) Kaynak koddan

1. **Python 3.11+** kur (python.org, kurulumda **Add Python to PATH** işaretli).
2. Depoyu indir (dal `claude/awesome-mccarthy-r3cum5` → **Code → Download ZIP**) ve aç.
3. `TradingviewSignalAgent\bot\kur.bat`'a çift tıkla. Masaüstüne **TSA Bot** kısayolu gelir.

macOS / Linux: `pip install -r requirements.txt -r requirements-desktop.txt` ve `python TSABot.pyw`.
Pencere açılamazsa arayüz tarayıcıda açılır. Sunucu ortamı için: `python -m tsabot` (yalnızca tarayıcı arayüzü).

**Ayarlar, şifreli anahtarlar ve işlem geçmişi** program klasöründe değil `%LOCALAPPDATA%\TSABot` içinde
durur: programı güncellemek için sadece program klasörünü değiştirmen yeter, hiçbir ayar kaybolmaz.

## Uygulama nasıl çalışır

- Pencereyi **X ile kapatınca uygulama kapanmaz**: saatin yanındaki gizli simgeler alanına (**^**) küçülür,
  bot çalışmaya devam eder. İlk seferde bunu hatırlatan bir bildirim çıkar.
- Tepsi simgesine tıklayınca pencere açılır. **Sağ tık** menüsü: *TSA Bot'u göster*, durum satırı,
  *Botu başlat*, *Botu durdur*, **Tamamen kapat**.
- *Tamamen kapat* açık pozisyon varsa sorar: kapanınca pozisyonları Binance'teki stop emirleri korumaya devam
  eder, ama ÇIK sinyali ve süre çıkışı takip edilmez. Bot çalışıyorduysa bir sonraki açılışta kaldığı yerden
  devam eder.
- Simgenin köşesindeki nokta: yeşil = çalışıyor, turuncu = yeni işlem kapalı, gri = durdu.
- İkinci kez açmaya çalışırsan yeni bir kopya başlamaz (iki bot aynı hesapta çift işlem yapardı); çalışan
  pencere öne gelir.
- **Mod ve uygulama** sayfasındaki seçenekler (bot için ayrılmış bilgisayar için):
  - *Windows açılınca TSA Bot'u başlat* (sistem tepsisinde, pencere açmadan)
  - *Uygulama açılınca botu devam ettir* (elektrik kesintisi / yeniden başlatma sonrası)
  - *Bot çalışırken bilgisayarın uykuya geçmesini engelle*
  - *Bu bilgisayarda beni hatırla*: şifre sorulmadan açılır. Anahtar Windows hesabına bağlı olarak
    (DPAPI) saklanır; dosya başka bir bilgisayara kopyalansa işe yaramaz. Canlı modda otomatik devam için gerekir.

## Başlat / durdur

| Düğme | Ne yapar |
|---|---|
| **Başlat** | Botu başlatır. Canlı modda şifreni bir kez daha ister. |
| **Durdur → Yeni işlem açma** (önerilen) | Yeni pozisyon açılmaz; açık pozisyonlar kurallarına göre (ÇIK sinyali, süre, stop) kapanmaya devam eder; hepsi kapanınca bot tamamen durur. Bu sırada **Başlat** düğmesi "Yeni işlemleri aç" olur. |
| **Durdur → Tamamen durdur** | Bot hemen durur; açık pozisyonlar sadece borsadaki stop emriyle korunur. |
| **Durdur → Tüm pozisyonları kapat ve durdur** / **Acil kapat** | Botun bütün pozisyonları piyasa fiyatından kapatılır, bot durur. |

## Pozisyonları elle kapatma

- Panel ve **Pozisyonlar** sayfasında her satırda **Kapat** düğmesi, üstte **Tümünü kapat** var.
  Onay sorulur; bot çalışmaya devam eder.
- Telefondan: `/kapat SOLUSDT` veya `/hepsinikapat`, ardından `/onay`.
- Sadece **botun** pozisyonları kapatılır; senin elle açtığın pozisyonlar listede ayrıca görünür ve dokunulmaz.

## Bot kârı kendisi alıyor mu? Evet, otomatik

Her pozisyon üç yoldan biriyle, sen bir şey yapmadan kapanır:

1. **ÇIK sinyali (kâr al):** her 5 dk kapanışında WaveTrend aşırı alım bölgesine geldiyse piyasa emriyle kapatır.
   Bu kural 3.240 alternatif içinden AL kuralının hiç görmediği 2017–2023 verisinde seçildi.
2. **Süre:** en geç 96 mum (8 saat) sonra kapatır.
3. **Zarar kes:** her işlemde Binance'e otomatik zarar-kes emri konur (3×ATR ile ayarladığın % stop'tan fiyata
   yakın olanı). Bot kapalıyken de çalışır.

Model şu an sadece AL (long) işlemi açar; SAT kuralı kilitli testten geçemedi. ÇIK sinyali, long pozisyonların
"sat" sinyalidir.

## Bütçe, kaldıraç ve stop (Bütçe & risk sayfası)

- **Futures cüzdanı** kutusu: USDT bakiyen, kullanılabilir bakiye, botun ve senin pozisyonlarının marjini.
- **Bütçe:** dolar olarak yaz, kaydırıcıyla ayarla ya da yüzde düğmelerine bas (futures USDT bakiyene göre).
  Bakiyenden fazlasını seçersen uyarı çıkar; bakiye yetmezse bot o sinyali atlar, fazlasını asla kullanmaz.
- Bütçeyi değiştirdiğin an **yeni bir bütçe dönemi** başlar: bot o anda tam bu bütçeye sahip olur.
  Zarar ederse kullanabileceği bütçe küçülür; kâr bütçeye eklenmez ("Kârı bütçeye ekle" işaretli değilse).
- **Kaldıraç** (1–10x, izole) ve **zarar-kes %**'si kaydırıcıyla elle ayarlanır. Stop'un üst sınırı kaldıraca
  göre kendiliğinden daralır (tasfiye stop'tan önce gelemez). Kaldıraç renk ile işaretlenir; 1x önerilir.
- Önizleme: pozisyon başı marjin, pozisyon büyüklüğü, stop olursa en fazla kayıp.
- Bu ayarlar **bot çalışırken de** değiştirilebilir; yeni açılan pozisyonlarda geçerli olur. Açık pozisyonlar
  kendi stop'unu korur. **Son kaydettiğin değerler kapatıp açınca da aynen kalır.**
- Günlük zarar limiti (o gün yeni işlem açılmaz) ve maksimum toplam zarar (bot kendini durdurur).

## Senin kendi işlemlerin: bot karışmaz

- Botun gönderdiği her emrin kimliği `tsa` ile başlar. Bot yalnızca bu emirleri iptal eder ya da değiştirir;
  "bir coindeki bütün emirleri iptal et" isteği programda hiç yok.
- Stop ve TP emirleri **sadece botun miktarı kadar** ve "reduce-only"dir: aynı coine elle ekleme yapsan bile
  botun stop'u senin kısmını kapatmaz.
- Elle pozisyon açtığın coinde bot işlem açmaz. O pozisyonu kapatınca coin bot için tekrar serbest kalır.
- Botun bir pozisyonunu Binance'te elle kısmen kapatırsan bot kalan miktarı yönetir (stop'u da ona göre
  yeniler); tamamen kapatırsan işlemi "borsada kapandı" diye kaydeder.
- Hesap **One-way** ve **Single-Asset** modunda olmalı (Hedge ve Multi-Assets reddedilir).

## Telegram

**Kurulum** (Telegram sayfası):

1. Telegram'da **@BotFather**'a `/newbot` yaz, bir isim ver; sana `123456789:AA…` gibi bir **token** verir.
2. Token'ı uygulamaya yapıştır → **Kaydet ve bağlan**.
3. **Eşleştir**'e bas; çıkan 6 haneli kodu botuna gönder (`/eslestir 123456`). Telefon eşleşir.
4. **Test mesajı gönder** ile dene.

**Komutlar** (Türkçe karakterle de yazılabilir: `/başlat`, `/geçmiş`):

| Komut | Ne yapar |
|---|---|
| `/rapor` | Bot durumu, bütçe, **bugünkü kasa** (kapanan + açık K/Z), açık pozisyonlar tek tek anlık K/Z |
| `/gecmis` | Bugünkü işlem geçmişi (her işlem, net $ ve %, neden, toplam, ödenen komisyon) · `/gecmis 7` son 7 gün |
| `/pozisyonlar` | Sadece açık pozisyonlar |
| `/baslat` · `/durdur` | Botu başlatır · yeni işlem açmayı durdurur |
| `/kapat SOLUSDT` · `/hepsinikapat` | Pozisyon kapatır, **`/onay`** ister (60 sn) |

**Bildirimler** (açılıp kapatılabilir): pozisyon açıldı / kapandı, hata, 10 dk'dan uzun bağlantı kesintisi ve
dönüşü, bot durdu, istersen her gün belirlediğin saatte günlük özet.

**Güvenlik:** bot yalnızca eşleşen sohbete cevap verir, başkalarına hiç cevap vermez. Eşleşme kodu 10 dk geçerli,
tek kullanımlık, 10 yanlış denemede iptal olur, sadece özel sohbetten kabul edilir. Uygulama kapalıyken
gönderilmiş eski komutlar (2 dk'dan eski) uygulanmaz. Telefondan kontrol ayarlardan kapatılabilir.

## Binance istek limitleri ve bağlantı sorunları

- Bot, Binance'in dakikalık limitinin (**2.400 ağırlık/dk**) en fazla **yarısını** kullanır; her isteğin
  ağırlığını Binance dokümanına göre hesaplar ve sınıra gelmeden bekler. Binance'in bildirdiği gerçek kullanım
  (aynı IP'deki başka programlar dahil) de hesaba katılır. Emir sayısı da sınırlıdır.
- Zarar kes / kapatma emirleri için ayrıca yedek pay vardır: limit dolsa bile koruma emirleri gider.
- 429/418 cevabında Binance'in söylediği süre kadar beklenir (hesabın banlanmaz).
- Normal kullanım: 36 coin için 5 dakikada ~80 ağırlık (sınırın %1'inden az).
- **Soket (websocket) kullanılmaz**: veri her 5 dk kapanışında ayrı isteklerle alınır, kopup takılı kalan bir
  bağlantı yoktur.
- İnternet ya da Binance geçici olarak gitmişse okuma istekleri kısa beklemelerle tekrarlanır; emirler ise
  sadece Binance'e hiç ulaşmadığı kesinse tekrar gönderilir (çift emir riski yok). Cevabı kaybolan emir
  diske yazılır ve bir sonraki mumda borsaya sorulur.
- Uzun kesintide bot durmaz, bağlantı gelince kaçırılan mumları indirip devam eder. Bu sırada açık
  pozisyonları borsadaki stop emirleri korur. Üst çubukta bağlantı durumu ve istek limiti kullanımı görünür.
- Bilgisayar saati kayarsa Binance saatine göre düzeltilir.

## Giriş yöntemi: neden piyasa emri (limit değil)

Aynı işlemleri "sinyal kapanış fiyatına post-only limit emir, 1 mum bekle" ile de hesapladım (maker komisyonu
%0,02, kayma yok; fiyat limitin altına inmedikçe dolmadı sayıldı):

| Dönem | İşlem | Limit dolma oranı | Toplam net (piyasa) | Toplam net (limit) |
|---|---|---|---|---|
| Kilitli test Şub–Eyl 2026 | 543 | %93 | +%230 | +%227 (daha kötü) |
| 2023-09 → 2026-02 | 1.537 | %98 | +%906 | +%974 |
| 2017–2023 | 4.282 | %97 | +%1.830 | +%1.912 |

(Toplam net = işlem başına net getirilerin toplamı.) Dolmayan işlemler ortalamanın yaklaşık iki katı
kazandırıyor: fiyat hemen yükselince limit dolmuyor, yani en iyi işlemler kaçıyor. Eski dönemlerde %4–8 fayda,
son dönemde küçük zarar; kısmi dolma riski de eklenince fark değişikliğe değmiyor. **Giriş piyasa emriyle kalıyor.**

## Önerilen sıra: Replay → Paper → Testnet → Canlı

| Mod | Ne yapar | Anahtar gerekir mi |
|---|---|---|
| **Replay** | Kayıtlı geçmiş mumları hızlıca oynatır (sadece araştırma verisi olan bilgisayarda) | Hayır |
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
4. Futures → Tercihler → **Position Mode: One-way**, **Asset Mode: Single-Asset**.
5. Uygulamada **API anahtarları** sayfasına key + secret + uygulama şifreni gir → *Şifreleyip kaydet* →
   *Canlı hesabı kontrol et*. Tüm satırlar yeşil olmalı.

Testnet anahtarı için Binance **Demo Trading** hesabında anahtar oluştur; bot `https://demo-fapi.binance.com`
adresini kullanır (eski <https://testnet.binancefuture.com> anahtarları için
`TSABOT_TESTNET_BASE=https://testnet.binancefuture.com`). Anahtarı ortam değişkeniyle de verebilirsin:
`BINANCE_API_KEY`, `BINANCE_API_SECRET` (dosyaya yazılmaz).

## Ne beklemeli (geçmiş simülasyon, 1.000 USDT, ÇIK politikası)

Rakamlar **net**: Binance taker komisyonu (%0.05 × 2), kayma (%0.02 × 2) ve gerçek funding ödemeleri düşülmüş.

| Dönem | 8 pozisyon, 1x (varsayılan) | Maks. düşüş | 8 pozisyon, 2x | Maks. düşüş | Aynı dönemde al-tut |
|---|---|---|---|---|---|
| Şubat–Eylül 2026 (kilitli test, 36 coin) | +%14.4 | %5.3 | +%28.8 | %9.8 | BTC +%21, sepet +%38 (düşüş %30) |
| Ocak 2025–Şubat 2026 (hiç kullanılmamış 10 coin) | +%33.4 | %29.4 | +%39.4 | %52.2 | sepet −%83 |

Bot zamanın yalnızca yaklaşık %11'inde pozisyonda. Kârlar olaylara bağlı (sert düşüş sonrası tepki alımları): kilitli
testte kârın çoğu Haziran 2026'daki birkaç günden geldi, sakin aylarda küçük zararlar olur. 3x kaldıraçta 2025'te
düşüş %69'a çıktı. Ayrıntı: [`../reports/SONUCLAR_5DK_BOT.md`](../reports/SONUCLAR_5DK_BOT.md) (Bölüm 11).

## Bot nasıl işlem yapar

1. Her **5 dakikalık mum kapanışında** (+3 sn) izlenen paritelerin son 1200 mumu ve 1 saatlik mumları alınır.
2. Araştırmadaki özellik kodunun **aynısı** ile göstergeler hesaplanır ve model kuralları kontrol edilir.
3. Sinyal varsa ve risk kuralları izin veriyorsa **piyasa emriyle** pozisyon açılır; emir dolar dolmaz
   Binance'e zarar-kes emri konur. Stop emri borsada yoksa bot bir sonraki mumda yeniden koyar; fiyat stop
   seviyesini geçmişse pozisyonu hemen kapatır.
4. Açık pozisyonların fiyatı 10 saniyede bir güncellenir (arayüz ve Telegram raporundaki anlık K/Z).
5. Tüm işlemler, komisyonlar ve net K/Z kaydedilir; panelde bütçe grafiği (bugün / 7 / 30 gün / tümü,
   üzerine gelince değer), günlük kâr/zarar çubukları ve işlem geçmişi (+ yeşil, − kırmızı) görünür.

Replay testi, botun kayıtlı veride **araştırma backtest'iyle aynı işlemleri** açıp kapattığını doğrular
(`tests/test_engine_replay.py`).

## Güvenlik özeti

| Önlem | Nerede |
|---|---|
| Uç nokta izin listesi (withdraw/transfer/deposit/convert… erişilemez) | `tsabot/exchange/binance.py` |
| Canlı modda çekim/transfer izinli anahtarı reddetme | `control.py` → `permission_check` |
| Kasa: rastgele veri anahtarı + şifreden türetilen anahtar (scrypt + Fernet); Binance ve Telegram bilgisi geri okunamaz | `tsabot/secrets.py` |
| "Beni hatırla": veri anahtarı Windows DPAPI ile bu Windows kullanıcısına bağlı | `secrets.py` → `Protector` |
| Loglarda anahtar/secret/imza/Telegram token maskeleme | `RedactFilter` |
| Telegram: sadece api.telegram.org ve izinli metodlar, tek eşleşmiş sohbet, onaylı kapatma | `tsabot/telegram.py` |
| Sadece 127.0.0.1, Host başlığı kontrolü, oturum çerezi + CSRF, sadece JSON, sıkı CSP | `api.py` |
| Arayüzde tüm veriler metin olarak basılır (XSS yok), dış kütüphane / CDN yok | `web/` |
| Kullanıcının emirleri/pozisyonları: bot sadece `tsa` kimlikli emirlerine ve kendi miktarına dokunur | `broker.py` |
| İstek ağırlığı bütçesi, 429/418 beklemesi, emirler asla körlemesine tekrar edilmez | `exchange/binance.py` |
| Her pozisyonun borsadaki stop emri başlangıçta ve her mumda doğrulanır | `engine.py` → `_protect` |
| Tek kopya çalışır (aynı hesapta çift işlem olmaz) | `winsys.py` |

## Testler

```bash
cd TradingviewSignalAgent/bot
python -m pytest -q
```

- `test_security.py`: izin listesi, imza, secret'ın hiçbir yanıtta / dosyada / logda görünmemesi, host/origin/CSRF,
  kaba kuvvet kilidi, yol aşımı, çekim izinli anahtarın reddi, bütçe ve limitler.
- `test_exchange_isolation.py`: istek limiti, güvenli tekrar, `tsa` etiketli emirler, kullanıcının stop'unun
  iptal edilmemesi, sadece bot miktarı kadar stop, elle yapılan değişikliklerin takibi.
- `test_control.py`: tek / toplu kapatma, durdurma şekilleri, çalışırken ayar değişikliği, bütçe dönemi,
  ayarların yeniden başlatmada korunması, kasa v2, "beni hatırla", cüzdan, geçmiş aralıkları.
- `test_telegram.py`: Türkçe komutlar, eşleşme güvenliği, onaylı kapatma, eski komutlar, bildirimler,
  token'ın hiçbir yerde görünmemesi, uygulama üzerinden uçtan uca akış.
- `test_desktop.py`: X ile tepsiye küçülme, tepsi menüsü, açık pozisyonla kapatma onayı, tek kopya,
  Windows başlangıç kaydı, gerçek sunucu, kendi kendini test.
- `test_ui.py`: gerçek Chromium ile **her sayfa ve düğme** (bütçe kaydırıcı/yüzde, kaldıraç/stop kalıcılığı,
  başlat/durdur/devam/acil kapat, tek ve toplu kapatma, grafikler, Telegram eşleşme, anahtarlar, telefon görünümü).
- `test_engine_replay.py`, `test_robustness.py`, `test_review_regressions.py`: motor = araştırma backtest'i,
  kesinti ve emir senaryoları.

## Önemli uyarılar

- Geçmiş performans geleceği garanti etmez; model kilitli testte kârlı olsa da canlı piyasada zarar edebilir.
  Kaldıraçlı işlemler sermayenin tamamını kaybettirebilir. Yatırım tavsiyesi değildir.
- Bilgisayar kapalıysa / internet kesikse bot çalışmaz (borsadaki stop emirleri yine çalışır). Bot için ayrılmış
  bilgisayarda "Windows açılınca başlat", "botu devam ettir", "uykuyu engelle" ve "beni hatırla"yı aç.
- Önce Paper ve Testnet'te dene; canlıda küçük bütçeyle başla.
