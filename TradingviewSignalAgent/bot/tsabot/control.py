"""Application controller: one place for everything the UI, the tray icon and Telegram can do.

Secrets, settings, bot start/stop, manual closes, wallet view, reports. The web API (api.py) only adds
authentication and input validation on top of it."""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import logging
import os
import secrets as _secrets
import sys
import time
from dataclasses import asdict
from pathlib import Path

from . import REPO_ROOT
from .broker import BinanceBroker, PaperBroker
from .config import AppPrefs, HOT_KEYS, Settings, TelegramPrefs, load_settings, save_settings
from .engine import Engine, day_start_ms
from .exchange.binance import BinanceFutures, ExchangeError
from .market import LiveMarket, ReplayMarket
from .secrets import REDACT, Protector, Secret, Vault, env_keys, write_private
from .store import Store
from .strategy import Strategy
from .telegram import TelegramAPI, TelegramService, valid_token

log = logging.getLogger("tsabot.control")
RANGES = {"today": None, "1d": 1, "7d": 7, "30d": 30, "90d": 90, "all": 0}


class ControlError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.msg, self.status = msg, status


class Controller:
    def __init__(self, data_dir: Path, model_path: Path, factories: dict | None = None,
                 protector: Protector | None = None, desktop: bool = False):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.data_dir, 0o700)
        except OSError:
            pass
        self.lock = asyncio.Lock()          # serialises start / stop / panic / settings / keys
        self.auth_path = self.data_dir / "auth.json"
        self.settings_path = self.data_dir / "settings.json"
        self.remember_path = self.data_dir / "remember.bin"
        self.vault = Vault(self.data_dir / "vault.json")
        self.store = Store(self.data_dir / "bot.db")
        self.model_path = Path(model_path)
        self.settings = load_settings(self.settings_path)
        self.sessions: dict[str, dict] = {}
        self.keys: tuple[Secret, Secret] | None = env_keys()
        self.keys_source = "env" if self.keys else None
        self.setup_token = None if self.auth_path.exists() else _secrets.token_urlsafe(24)
        self.fail_count = 0
        self.locked_until = 0.0
        self.engine: Engine | None = None
        self.last_check: dict = {}
        self.factories = factories or {}
        self.protector = protector or Protector()
        self.desktop = desktop
        self.desktop_token: str | None = _secrets.token_urlsafe(32) if desktop else None
        self.dek: bytes | None = None
        self.app_prefs = AppPrefs.from_dict(self.store.get("prefs:app"))
        self.tg_prefs = TelegramPrefs.from_dict(self.store.get("prefs:telegram"))
        self.tg_token: Secret | None = None
        self.telegram = TelegramService(self, api_factory=self.factories.get("telegram_api", TelegramAPI))
        self.hooks: list = []               # desktop shell: fn(kind, data) for tray notifications / icon state
        self.prefs_hooks: list = []         # desktop shell: fn(AppPrefs) (Windows autostart ...)
        self._wallet: tuple[float, str, dict | None] = (0.0, "", None)
        self.loop: asyncio.AbstractEventLoop | None = None
        if self.keys:
            REDACT.add(*self.keys)

    # ================================================================== helpers
    @property
    def mode(self) -> str:
        return self.engine.mode if self.engine else self.settings.mode

    def now_ms(self) -> int:
        if self.engine is not None:
            return self.engine._now()
        return int(time.time() * 1000)

    def replay_available(self) -> bool:
        return (REPO_ROOT / "data" / "raw" / "BTCUSDT_5m.parquet").exists()

    def app_info(self) -> dict:
        from . import __version__
        return {"desktop": self.desktop, "platform": sys.platform, "version": __version__,
                "replay_available": self.replay_available(), "remember_available": self.protector.available,
                "remembered": self.remember_path.exists(), "data_dir": str(self.data_dir)}

    # ================================================================== secrets
    def unlock(self, password: str) -> bool:
        """After a successful password check: open (or create) the vault and load the secrets."""
        dek = self.vault.unlock(password) if self.vault.exists() else self.vault.create(password)
        if dek is None:
            log.error("vault could not be opened with the login password")
            return False
        self._use_dek(dek)
        return True

    def _use_dek(self, dek: bytes) -> None:
        self.dek = dek
        if not self.vault.get(dek, "_check"):
            self.vault.put(dek, "_check", {"ok": 1})
        if self.keys_source != "env":
            b = self.vault.get(dek, "binance")
            if b:
                self.keys, self.keys_source = (Secret(b["k"]), Secret(b["s"])), "vault"
                REDACT.add(*self.keys)
        t = self.vault.get(dek, "telegram")
        if t and valid_token(t.get("token", "")):
            self.tg_token = Secret(t["token"])
            REDACT.add(self.tg_token)
        self._schedule(self.ensure_telegram())

    def auto_unlock(self) -> bool:
        """Desktop start with 'remember me': the vault key is protected by Windows (DPAPI) for this user."""
        if self.dek is not None:
            return True
        if not (self.remember_path.exists() and self.protector.available and self.vault.exists()):
            return False
        try:
            dek = self.protector.unprotect(base64.b64decode(self.remember_path.read_bytes()))
        except Exception as e:
            log.warning("remembered key could not be opened: %s", type(e).__name__)
            return False
        if not self.vault.get(dek, "_check"):
            return False
        self._use_dek(dek)
        return True

    def set_remember(self, on: bool) -> bool:
        if on:
            if self.dek is None or not self.protector.available:
                return False
            write_private(self.remember_path, base64.b64encode(self.protector.protect(self.dek)).decode())
            return True
        if self.remember_path.exists():
            self.remember_path.unlink()
        return False

    def save_keys(self, password: str, api_key: str, api_secret: str) -> None:
        dek = self.dek or (self.vault.unlock(password) if self.vault.exists() else self.vault.create(password))
        if dek is None:
            raise ControlError("Kasa açılamadı", 500)
        self.dek = dek
        self.vault.put(dek, "binance", {"k": api_key, "s": api_secret})
        self.keys, self.keys_source = (Secret(api_key), Secret(api_secret)), "vault"
        REDACT.add(*self.keys)
        self.last_check = {}
        self._wallet = (0.0, "", None)

    def delete_keys(self) -> None:
        self.vault.remove("binance")
        if self.keys_source == "vault":
            self.keys, self.keys_source = None, None
        self.last_check = {}

    # ================================================================== engine
    def build_engine(self) -> Engine:
        s = Settings.from_dict(asdict(self.settings))
        strategy = Strategy.load(self.model_path)
        if "engine" in self.factories:
            eng = self.factories["engine"](s, strategy, self)
        else:
            if s.mode == "replay":
                market, broker = ReplayMarket(s.symbols, speed_s=s.replay_speed), PaperBroker()
            elif s.mode == "paper":
                market, broker = LiveMarket(BinanceFutures("live")), PaperBroker()
            else:
                client = BinanceFutures(s.mode, *self.keys)
                market, broker = LiveMarket(client), BinanceBroker(client)
            eng = Engine(s, strategy, market, broker, self.store)
        if hasattr(eng, "listeners"):
            eng.listeners.append(self._on_engine_event)
        return eng

    def client_for(self, venue: str) -> BinanceFutures:
        if "client" in self.factories:
            return self.factories["client"](venue, self.keys)
        return BinanceFutures(venue, *self.keys)

    def _on_engine_event(self, kind: str, data: dict) -> None:
        self.telegram.notify(kind, data)
        for fn in list(self.hooks):
            try:
                fn(kind, data)
            except Exception as e:
                log.warning("hook failed: %s", e)

    def emit_state(self) -> None:
        self._on_engine_event("state", {"state": getattr(self.engine, "state", "stopped") if self.engine else "stopped"})

    async def permission_check(self, venue: str) -> dict:
        if not self.keys:
            return {"venue": venue, "ok": False, "error": "API anahtarı yok"}
        client = self.client_for(venue)
        try:
            out = {"venue": venue, "ts": int(time.time() * 1000)}
            await client.sync_time()
            acc = await client.account()
            out["available_usdt"] = float(acc.get("availableBalance", 0))
            out["dual_side"] = await client.dual_side()
            if venue == "live":
                r = await client.api_restrictions()
                need = ("enableFutures", "enableWithdrawals", "enableInternalTransfer", "permitsUniversalTransfer")
                if any(k not in r for k in need):   # fail closed
                    out.update(ok=False, error="Anahtar izinleri okunamadı; güvenlik için canlı işlem yapılmaz.")
                    return out
                out.update(futures=bool(r.get("enableFutures")), withdrawals=bool(r.get("enableWithdrawals")),
                           internal_transfer=bool(r.get("enableInternalTransfer")),
                           universal_transfer=bool(r.get("permitsUniversalTransfer")),
                           ip_restricted=bool(r.get("ipRestrict")))
                bad = [k for k in ("withdrawals", "internal_transfer", "universal_transfer") if out[k]]
                if bad:
                    out.update(ok=False, error="Bu anahtarda para çekme/transfer izni açık. Güvenlik için bot bu "
                                               "anahtarla canlı işlem yapmaz; Binance'te bu izinleri kapatın.")
                    return out
                if not out["futures"]:
                    out.update(ok=False, error="Anahtarda 'Enable Futures' izni kapalı.")
                    return out
            out["ok"] = True
            return out
        except ExchangeError as e:
            return {"ok": False, "error": REDACT.clean(str(e))}
        except Exception as e:
            return {"ok": False, "error": REDACT.clean(f"{type(e).__name__}: {e}")[:300]}
        finally:
            await client.close()

    async def start_bot(self, source: str = "ui", password_ok: bool = True) -> dict:
        """Starts the bot, or re-enables new entries of a bot that was stopped with 'Durdur'.
        UI starts in live mode need the password (checked by the API before `password_ok`)."""
        async with self.lock:
            eng = self.engine
            if eng is not None and eng.running:
                if not getattr(eng, "entries", True):
                    await eng.start()
                    eng.emit("info", "Yeni işlem açma yeniden etkin")
                self.store.put("bot:desired", True)
                self.emit_state()
                return {"ok": True, "running": True}
            cfg = self.settings
            if cfg.mode in ("testnet", "live") and not self.keys:
                raise ControlError("Bu mod için API anahtarı gerekli (API anahtarları sayfası)"
                                   + ("" if source == "ui" else "; uygulamada giriş yapılmamış olabilir"))
            if cfg.mode == "live":
                if not cfg.live_confirmed:
                    raise ControlError("Canlı işlem için Ayarlar'da 'Gerçek parayla işlem yapılacağını onaylıyorum' "
                                       "kutusunu işaretleyin")
                if not password_ok:
                    raise ControlError("Canlı başlatmak için arayüz şifresini girin", 401)
                chk = await self.permission_check("live")
                self.last_check = chk
                if not chk.get("ok"):
                    raise ControlError(chk.get("error", "Anahtar kontrolü başarısız"))
            if cfg.mode == "replay" and not self.replay_available():
                raise ControlError("Replay için araştırma verisi bu bilgisayarda yok; Paper modunu kullanın")
            if not self.model_path.exists():
                raise ControlError("Model dosyası bulunamadı", 500)
            eng = self.build_engine()
            await eng.start()
            self.engine = eng
            self.store.put("bot:desired", True)
            if source != "ui" and hasattr(eng, "emit"):
                eng.emit("info", {"telegram": "Bot Telegram'dan başlatıldı", "tray": "Bot sistem tepsisinden başlatıldı",
                                  "resume": "Bot, uygulama açılışında kaldığı yerden başlatıldı"}.get(source, "Bot başlatıldı"))
            self.emit_state()
            return {"ok": True, "running": True}

    async def stop_bot(self, how: str = "drain") -> dict:
        """drain: no new entries, open positions still closed by their rules (then the bot stops);
        full: loop stops at once (positions keep their exchange stop); close: close everything and stop."""
        if how not in ("drain", "full", "close"):
            raise ControlError("Geçersiz durdurma şekli")
        async with self.lock:
            self.store.put("bot:desired", False)
            if how == "close":
                return await self._panic()
            eng = self.engine
            if eng is None or not eng.running:
                self.emit_state()
                return {"ok": True, "running": False, "state": "stopped"}
            if how == "full":
                await eng.stop()
                eng.emit("info", "Bot tamamen durduruldu" + (f"; {len(eng.positions)} açık pozisyon borsadaki stop "
                                                               "emriyle korunuyor" if eng.positions else ""))
                state = "stopped"
            else:
                state = await eng.pause()
            self.emit_state()
            return {"ok": True, "running": eng.running, "state": state}

    async def _panic(self) -> dict:
        if self.engine is None or not self.engine.running:
            persisted = self.store.get(f"positions:{self.settings.mode}", {}) or {}
            if not persisted:
                self.emit_state()
                return {"ok": True, "running": False, "closed": 0}
            if self.settings.mode in ("testnet", "live") and not self.keys:
                raise ControlError("Kayıtlı pozisyonları kapatmak için API anahtarı gerekli")
            self.engine = self.build_engine()        # restores the persisted positions
        n = len(self.engine.positions)
        await self.engine.panic()
        self.emit_state()
        return {"ok": True, "running": False, "closed": n - len(self.engine.positions)}

    async def panic(self) -> dict:
        async with self.lock:
            self.store.put("bot:desired", False)
            return await self._panic()

    async def close_positions(self, symbols=None, reason: str = "Elle kapatıldı") -> dict:
        """Close bot positions (all or some) while the bot keeps running (or while it is stopped)."""
        async with self.lock:
            if self.engine is None:
                persisted = self.store.get(f"positions:{self.settings.mode}", {}) or {}
                if not persisted:
                    return {s: "Botun bu coinde açık pozisyonu yok" for s in (symbols or [])}
                if self.settings.mode in ("testnet", "live") and not self.keys:
                    raise ControlError("Pozisyonu kapatmak için API anahtarı gerekli (giriş yapın)")
                self.engine = self.build_engine()
            res = await self.engine.close_positions(symbols, reason)
            self._wallet = (0.0, "", None)
            return res

    async def remote_start(self, source: str) -> str:
        try:
            await self.start_bot(source)
        except ControlError as e:
            return "⚠️ Başlatılamadı: " + e.msg
        s = self.settings
        return (f"▶️ Bot çalışıyor ({s.mode.upper()}) · bütçe {s.budget_usdt:.2f} $ · kaldıraç {s.leverage}x · "
                f"stop %{s.emergency_stop_pct:g} · {len(s.symbols)} USDT paritesi")

    async def remote_stop(self) -> str:
        r = await self.stop_bot("drain")
        n = len(self.engine.positions) if self.engine else 0
        if r.get("state") == "draining":
            return (f"⏸ Yeni işlem açma durduruldu. {n} açık pozisyon kurallarına göre (ÇIK sinyali / süre / stop) "
                    "kapanınca bot tamamen durur.\nHepsini hemen kapatmak için /hepsinikapat")
        return "⏹ Bot durduruldu. Açık pozisyon yok."

    # ================================================================== settings
    async def update_settings(self, d: dict) -> Settings:
        async with self.lock:
            new = Settings.from_dict({**asdict(self.settings), **d})
            err = new.validate()
            if err:
                raise ControlError("; ".join(err), 422)
            # the live confirmation must be given explicitly, in live mode, in this very request
            new.live_confirmed = new.mode == "live" and d.get("live_confirmed") is True
            if new.budget_usdt != self.settings.budget_usdt:
                new.budget_set_at = self.now_ms()           # a new budget starts a new budget period
            else:
                new.budget_set_at = self.settings.budget_set_at
            changed = {k for k, v in asdict(new).items() if asdict(self.settings)[k] != v}
            eng = self.engine
            if eng is not None and eng.running:
                cold = changed - HOT_KEYS
                if cold:
                    raise ControlError("Mod ve parite listesi bot dururken değiştirilebilir: önce botu durdurun ("
                                       + ", ".join(sorted(cold)) + ")", 409)
                if new.mode == "live" and not new.live_confirmed:
                    raise ControlError("Canlı mod çalışırken onay kutusu kaldırılamaz; önce botu durdurun", 409)
                eng.apply_settings(Settings.from_dict(asdict(new)))
                if changed:
                    eng.emit("info", "Ayarlar çalışırken güncellendi: " + ", ".join(
                        f"{k}={getattr(new, k)}" for k in sorted(changed - {'budget_set_at', 'live_confirmed'})))
            self.settings = new
            save_settings(new, self.settings_path)
            self._wallet = (0.0, "", None)
            return new

    def save_app_prefs(self, d: dict) -> AppPrefs:
        self.app_prefs = AppPrefs.from_dict({**asdict(self.app_prefs), **d})
        self.store.put("prefs:app", asdict(self.app_prefs))
        for fn in list(self.prefs_hooks):
            try:
                fn(self.app_prefs)
            except Exception as e:
                log.warning("prefs hook failed: %s", e)
        return self.app_prefs

    # ================================================================== telegram
    def save_tg_prefs(self) -> None:
        self.store.put("prefs:telegram", asdict(self.tg_prefs))

    def on_telegram_identity(self, me: dict) -> None:
        self.tg_prefs.bot_username = str(me.get("username") or "")[:64]
        self.save_tg_prefs()

    def on_telegram_paired(self, chat_id: int, name: str) -> None:
        self.tg_prefs.chat_id, self.tg_prefs.chat_name = chat_id, name
        self.save_tg_prefs()
        if self.engine:
            self.engine.emit("info", f"Telegram eşleşti: {name}")

    async def set_telegram_token(self, token: str) -> dict:
        if self.dek is None:
            raise ControlError("Önce giriş yapın", 409)
        if not valid_token(token):
            raise ControlError("Token biçimi geçersiz (BotFather'ın verdiği 123456789:AA… şeklindeki metin)", 422)
        sec = Secret(token)
        REDACT.add(sec)
        try:
            me = await self.telegram.check_token(sec)
        except Exception as e:
            raise ControlError("Telegram bu token'ı kabul etmedi: " + REDACT.clean(str(getattr(e, "desc", e)))[:150])
        if self.tg_token is None or self.tg_token.reveal() != token:
            self.tg_prefs.chat_id, self.tg_prefs.chat_name = None, ""   # a new bot must be paired again
        self.vault.put(self.dek, "telegram", {"token": token})
        self.tg_token = sec
        self.on_telegram_identity(me)
        await self.telegram.start(sec)
        return self.telegram.view()

    async def delete_telegram(self) -> None:
        await self.telegram.stop()
        self.vault.remove("telegram")
        self.tg_token = None
        self.tg_prefs = TelegramPrefs()
        self.save_tg_prefs()

    async def ensure_telegram(self) -> None:
        if self.tg_token is not None and self.telegram.task is None:
            await self.telegram.start(self.tg_token)

    def _schedule(self, coro) -> None:
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            coro.close()                      # no loop yet: started by on_startup()

    # ================================================================== lifecycle of the app
    async def on_startup(self) -> None:
        self.loop = asyncio.get_running_loop()
        if self.desktop:
            self.auto_unlock()
        await self.ensure_telegram()
        if self.app_prefs.resume_bot and self.store.get("bot:desired"):
            try:
                await self.start_bot("resume")
            except ControlError as e:
                log.warning("resume failed: %s", e.msg)
                self.store.event("warn", f"Bot açılışta otomatik başlatılamadı: {e.msg}")
                self._on_engine_event("error", {"msg": f"Bot açılışta otomatik başlatılamadı: {e.msg}",
                                                "mode": self.settings.mode})

    async def on_shutdown(self) -> None:
        if self.engine and self.engine.running:
            await self.engine.stop()          # "desired" stays as it is: the bot resumes next time
        try:                                  # the "bot stopped" message still reaches the phone
            await asyncio.wait_for(self.telegram._flush(), timeout=5)
        except Exception:
            pass
        await self.telegram.stop()

    # ================================================================== views
    def snapshot(self) -> dict:
        if self.engine:
            snap = self.engine.snapshot()
        else:
            m, s = self.settings.mode, self.settings
            day = day_start_ms(self.now_ms())
            period = self.store.realized(m, int(s.budget_set_at or 0))
            persisted = list((self.store.get(f"positions:{m}", {}) or {}).values())
            for p in persisted:
                p.setdefault("unrealized", None)
                p.setdefault("last_price", None)
            snap = {"running": False, "state": "stopped", "entries": False, "status": "Durduruldu", "mode": m,
                    "budget": s.budget_usdt, "effective_budget": s.budget_usdt + min(period, 0),
                    "budget_set_at": s.budget_set_at,
                    "margin_used": sum(float(p.get("margin") or 0) for p in persisted),
                    "realized": self.store.realized(m), "realized_period": period,
                    "realized_today": self.store.realized(m, day), "unrealized": 0.0, "positions": persisted,
                    "last_bar": None, "price_ts": None, "now": self.now_ms(), "symbols": len(s.symbols),
                    "leverage": s.leverage,
                    "max_positions": s.max_positions, "emergency_stop_pct": s.emergency_stop_pct, "external": [],
                    "net": {}, "stats": self.store.stats(m)}
        snap["keys"] = {"unlocked": self.keys is not None, "source": self.keys_source}
        snap["telegram"] = {"paired": self.tg_prefs.chat_id is not None, "status": self.telegram.status}
        return snap

    def range_since(self, rng: str) -> int:
        if rng not in RANGES:
            raise ControlError("Geçersiz aralık", 422)
        now = self.now_ms()
        if rng == "today":
            return day_start_ms(now)
        days = RANGES[rng]
        return 0 if not days else now - days * 86_400_000

    def trades_in(self, rng: str = "all", limit: int = 500) -> list[dict]:
        return self.store.trades(self.mode, limit, since_ms=self.range_since(rng))

    def trade_summary(self, rng: str = "all") -> dict:
        return self.store.summary(self.mode, since_ms=self.range_since(rng))

    def daily(self, days: int = 30) -> list[dict]:
        """Net result per local calendar day (the computer's time zone)."""
        now = self.now_ms()
        off = dt.datetime.fromtimestamp(now / 1000, dt.timezone.utc).astimezone().utcoffset()
        tzoff = int(off.total_seconds() * 1000) if off else 0
        return self.store.daily_pnl(self.mode, day_start_ms(now) - (days - 1) * 86_400_000, tzoff)

    def equity(self, rng: str = "7d") -> list[dict]:
        since = self.range_since(rng) if rng != "today" else day_start_ms(self.now_ms())
        return self.store.equity(self.mode, limit=200_000, since_ms=since, points=500)

    async def wallet(self, force: bool = False) -> dict:
        """Futures wallet with the bot's and the user's own positions (cached 15 s, weight 10)."""
        snap = self.snapshot()
        bot_syms = {p["symbol"] for p in snap["positions"]}
        bot_margin = float(snap["margin_used"] or 0)
        m = self.mode
        if m in ("paper", "replay"):
            w = self.settings.paper_wallet_usdt + float(snap["realized"] or 0)
            return {"source": m, "wallet": w, "available": w - bot_margin, "unrealized": snap["unrealized"],
                    "bot_margin": bot_margin, "user_margin": 0.0, "allocatable": w,
                    "positions": [{"symbol": p["symbol"], "amount": p["direction"] * p["qty"],
                                   "entry_price": p["entry_price"], "mark_price": p.get("last_price"),
                                   "unrealized": p.get("unrealized"), "margin": p["margin"], "owner": "bot"}
                                  for p in snap["positions"]], "ts": int(time.time() * 1000)}
        if not self.keys:
            return {"source": m, "error": "Cüzdanı görmek için API anahtarı gerekli"}
        ts, cm, cached = self._wallet
        if cached is not None and cm == m and not force and time.time() - ts < 15:
            return cached
        eng = self.engine
        temp = None
        try:
            if eng is not None and isinstance(eng.broker, BinanceBroker):
                broker = eng.broker
            else:
                temp = self.client_for(m)
                broker = BinanceBroker(temp)
            v = await broker.account_view()
        except ExchangeError as e:
            return {"source": m, "error": REDACT.clean(str(e))[:200]}
        except Exception as e:
            return {"source": m, "error": REDACT.clean(f"{type(e).__name__}: {e}")[:200]}
        finally:
            if temp is not None:
                await temp.close()
        for p in v["positions"]:
            p["owner"] = "bot" if p["symbol"] in bot_syms else "user"
        user_margin = sum(p["margin"] for p in v["positions"] if p["owner"] == "user")
        out = {**v, "source": m, "bot_margin": bot_margin, "user_margin": user_margin,
               "allocatable": v["available"] + bot_margin, "ts": int(time.time() * 1000)}
        self._wallet = (time.time(), m, out)
        return out
