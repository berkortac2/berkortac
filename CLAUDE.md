# berkortac — Claude Code rehberi

Bu repoda tek aktif proje var: `TradingviewSignalAgent/`. Kullanıcıyla Türkçe konuş; kod, yorum ve commit
mesajları İngilizce, kullanıcının gördüğü metinler (UI, Telegram, README, tepsi) Türkçe.

## Proje

- `TradingviewSignalAgent/` (araştırma): Binance USDT-M perpetual verisiyle mum-mum "replay" eğitimi,
  yöntem araması, kilitli test, Pine v6 üretimi. Kod `src/tsa/`, adımlar `scripts/01…19_*.py`, sonuçlar
  `reports/` (özet: `reports/SONUCLAR.md`, `reports/SONUCLAR_5DK_BOT.md`), Pine dosyaları `pine/`.
- `TradingviewSignalAgent/bot/` (TSA Bot): 5 dakikalık modelle Binance USDT-M futures botu + Windows masaüstü
  uygulaması (pywebview penceresi, tepsi simgesi, Telegram uzaktan kumanda). Ayrıntılar `bot/README.md`.
  - `tsabot/exchange/binance.py`: REST istemcisi, izin listesi, istek-ağırlığı limiti, güvenli tekrar
  - `tsabot/broker.py` (Binance/Paper), `market.py` (Live/Replay), `engine.py` (sinyal → emir döngüsü),
    `strategy.py` (model kuralları), `risk.py`
  - `tsabot/control.py` (kasa açma, başlat/durdur, ayarlar, cüzdan), `api.py` (FastAPI, yalnızca 127.0.0.1)
  - `tsabot/secrets.py` (şifreli kasa, DPAPI "beni hatırla", log maskeleme), `store.py` (SQLite geçmiş)
  - `tsabot/telegram.py`, `reports.py` (Telegram), `desktop.py`, `winsys.py` (pencere, tepsi, Windows)
  - `web/` (cam tasarımlı arayüz: `index.html`, `app.css`, `app.js`), `model/model_5m.json` (kullanılan model)
  - `packaging/tsabot.spec` (PyInstaller), `kur.bat` (kaynaktan kurulum), `TSABot.pyw` (başlatıcı)

## Değişmez kurallar (kullanıcının açık talepleri)

1. Bot **hiçbir şekilde para çekme (withdraw) veya transfer yapmaz.** `exchange/binance.py` içindeki uç nokta
   izin listesine withdraw/transfer/wallet uçları eklenmez; çekim ya da transfer izni açık API anahtarı reddedilir.
2. **Binance API anahtarı dışarı sızmaz:** şifreli kasa dışında diske, loga, API yanıtına, Telegram'a,
   hata mesajına yazılmaz. Arayüz yalnızca 127.0.0.1'de çalışır. `tests/test_security.py` bunu doğrular.
3. Sadece **USDT pariteleri** (USDT-M perpetual, `SYMBOL_RE = ^[A-Z0-9]{2,20}USDT$`).
4. Bot, kullanıcının Binance'te elle açtığı pozisyonlara ve emirlerine **dokunmaz**: botun emirleri `tsa`
   önekiyle etiketlenir, sadece bunlar iptal edilir, stoplar sadece botun miktarı kadardır.
5. Bot, kullanıcının belirlediği bütçeyi **aşmaz**; kaldıraç, stop %, bütçe gibi ayarlar yeniden başlatmada korunur.
6. Binance istek limitleri: dakikada en fazla 1200 ağırlık (Binance sınırı 2400), 2000'de kritik mod;
   emir tarafında 10 saniyede 50. Emir (POST) sadece hiç gönderilmediği kesinse yeniden denenir.

## Geliştirme ortamı

```bash
cd TradingviewSignalAgent/bot
python -m venv .venv                        # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-desktop.txt
python -m pytest -q                         # UI testleri Playwright + Chromium ister (python -m playwright install chromium)
python -m pytest -q --ignore=tests/test_ui.py
python TSABot.pyw                           # masaüstü uygulaması (Windows)
python -m tsabot                            # sadece web arayüzü: http://127.0.0.1:8765
```

- **Geliştirme verisini gerçek botunkinden ayır:** uygulama verisi (ayarlar, şifreli anahtarlar, işlem
  geçmişi) varsayılan olarak `%LOCALAPPDATA%\TSABot` içindedir; kurulu TSABot.exe de aynı klasörü kullanır.
  Geliştirirken `TSABOT_DATA_DIR` ortam değişkenini ayrı bir klasöre ver (ör. `set TSABOT_DATA_DIR=%CD%\devdata`)
  ve geliştirme kopyasını paper modunda çalıştır.
- Masaüstü uygulaması tek kopya çalışır (sabit adlı Windows mutex'i): kurulu TSABot.exe açıkken
  `python TSABot.pyw` yeni pencere açmaz. Aynı PC'de denerken `python -m tsabot --port 8766` kullan.
- Araştırma kısmı: `cd TradingviewSignalAgent && pip install -r requirements.txt && python -m pytest -q`.
  `data/` (~25 GB) repoda yok; gerekirse `scripts/01_download.py`, `02_build_features.py`,
  `08_extend_5m_data.py` ile yeniden üretilir. Veri yoksa ilgili testler kendiliğinden atlanır.

## CI ve teslim

- `.github/workflows/tsabot-windows.yml`: `bot/**` değişince Windows'ta testleri çalıştırır, TSABot.exe'yi
  derler, `--selftest` ve `--guitest` ile dener ve `TSABot-windows` artifact'ını yükler (90 gün).
  Tetikleyici dal listesini, çalışılan dala göre güncel tut.
- Değişiklikten sonra ilgili testleri çalıştır. Arayüzde bir tuş/akış değiştiyse `tests/test_ui.py`'yi de
  güncelle. Strateji mantığı değişirse `tests/test_engine_replay.py` (replay paritesi) geçmeli.
- Model kuralları (`model_5m.json`, AL kuralı, ÇIK çıkışı, 3×ATR/%8 acil stop, 96 mum zaman çıkışı) araştırma
  sonuçlarına dayanır; veriyle ölçmeden değiştirme.
