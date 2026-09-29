"""Local web API + UI server.

Security model
* binds to 127.0.0.1 only; Host header must be localhost/127.0.0.1 (DNS-rebinding guard)
* first run: password set with a one-time setup token printed on the console
* session cookie (HttpOnly, SameSite=Strict) + CSRF header on every state change,
  JSON-only bodies, same-origin check, strict CSP, no CORS
* API secret: write-only; stored encrypted with a key derived from the UI password
  (scrypt + Fernet) or read from environment variables; never returned or logged
* live trading refuses keys that can withdraw or transfer funds
"""
from __future__ import annotations

import asyncio
import hmac
import json
import re
import secrets as _secrets
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import BOT_ROOT
from .broker import BinanceBroker, PaperBroker
from .config import DATA_DIR, MODEL_PATH, Settings, load_settings, save_settings
from .engine import Engine
from .exchange.binance import BinanceFutures, ExchangeError
from .market import LiveMarket, ReplayMarket
from .secrets import REDACT, Secret, Vault, env_keys, hash_password, verify_password, write_private
from .store import Store
from .strategy import Strategy

WEB = BOT_ROOT / "web"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
SESSION_TTL = 12 * 3600
KEY_RE = re.compile(r"^[A-Za-z0-9]{16,128}$")
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "font-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


class SetupBody(BaseModel):
    token: str = Field(max_length=128)
    password: str = Field(min_length=10, max_length=256)


class LoginBody(BaseModel):
    password: str = Field(max_length=256)


class KeysBody(BaseModel):
    password: str = Field(max_length=256)
    api_key: str = Field(max_length=128)
    api_secret: str = Field(max_length=128)


class CheckBody(BaseModel):
    venue: str = Field(pattern="^(live|testnet)$")


class AppState:
    def __init__(self, data_dir: Path, model_path: Path, factories: dict | None = None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.auth_path = self.data_dir / "auth.json"
        self.settings_path = self.data_dir / "settings.json"
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
        if self.keys:
            REDACT.add(*self.keys)

    # ------------------------------------------------------------ builders
    def build_engine(self) -> Engine:
        s = Settings.from_dict(asdict(self.settings))
        strategy = Strategy.load(self.model_path)
        if "engine" in self.factories:
            return self.factories["engine"](s, strategy, self)
        if s.mode == "replay":
            market = ReplayMarket(s.symbols, speed_s=s.replay_speed)
            broker = PaperBroker()
        elif s.mode == "paper":
            market = LiveMarket(BinanceFutures("live"))
            broker = PaperBroker()
        else:
            client = BinanceFutures(s.mode, *self.keys)
            market = LiveMarket(client)
            broker = BinanceBroker(client)
        return Engine(s, strategy, market, broker, self.store)

    def client_for(self, venue: str) -> BinanceFutures:
        if "client" in self.factories:
            return self.factories["client"](venue, self.keys)
        return BinanceFutures(venue, *self.keys)


def create_app(data_dir: Path = DATA_DIR, model_path: Path = MODEL_PATH, factories: dict | None = None) -> FastAPI:
    st = AppState(data_dir, model_path, factories)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        if st.engine and st.engine.running:
            await st.engine.stop()

    app = FastAPI(title="TSA Bot", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.tsa = st

    # ------------------------------------------------------------ middleware
    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in ALLOWED_HOSTS:
            return PlainTextResponse("Invalid host", status_code=400)
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).hostname not in ALLOWED_HOSTS:
                return PlainTextResponse("Cross-origin request refused", status_code=403)
            if not (request.headers.get("content-type") or "").startswith("application/json"):
                return PlainTextResponse("JSON body required", status_code=415)
            cl = request.headers.get("content-length")
            if cl is None or not cl.isdigit() or int(cl) > 16_384:
                return PlainTextResponse("Body too large or missing length", status_code=413)
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # ------------------------------------------------------------ auth helpers
    def session(request: Request) -> dict:
        tok = request.cookies.get("tsa_session", "")
        s = st.sessions.get(tok)
        if not s or s["exp"] < time.time():
            st.sessions.pop(tok, None)
            raise HTTPException(401, "Oturum gerekli")
        if request.method != "GET":
            if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), s["csrf"]):
                raise HTTPException(403, "CSRF doğrulaması başarısız")
        s["exp"] = time.time() + SESSION_TTL
        return s

    def new_session(resp: JSONResponse) -> None:
        tok, csrf = _secrets.token_urlsafe(32), _secrets.token_urlsafe(32)
        st.sessions[tok] = {"exp": time.time() + SESSION_TTL, "csrf": csrf}
        resp.set_cookie("tsa_session", tok, httponly=True, samesite="strict", max_age=SESSION_TTL, path="/")
        resp.headers["X-CSRF-Token"] = csrf

    def check_password(pw: str) -> bool:
        now = time.time()
        if now < st.locked_until:
            raise HTTPException(429, f"Çok fazla hatalı deneme. {int(st.locked_until - now) + 1} sn bekleyin.")
        ok = st.auth_path.exists() and verify_password(pw, json.loads(st.auth_path.read_text()))
        if ok:
            st.fail_count = 0
            return True
        st.fail_count += 1
        if st.fail_count >= 5:
            st.locked_until = now + min(900, 30 * 2 ** (st.fail_count - 5))
        return False

    def unlock_vault(pw: str) -> None:
        if st.keys_source != "env" and st.vault.exists():
            k = st.vault.load(pw)
            if k:
                st.keys, st.keys_source = k, "vault"
                REDACT.add(*k)

    # ------------------------------------------------------------ pages
    @app.get("/")
    async def index():
        return FileResponse(WEB / "index.html")

    app.mount("/static", StaticFiles(directory=WEB), name="static")

    # ------------------------------------------------------------ auth
    @app.get("/api/auth/state")
    async def auth_state(request: Request):
        tok = request.cookies.get("tsa_session", "")
        s = st.sessions.get(tok)
        return {"setup_required": st.setup_token is not None, "logged_in": bool(s and s["exp"] > time.time())}

    @app.post("/api/auth/setup")
    async def setup(body: SetupBody):
        if st.setup_token is None:
            raise HTTPException(409, "Şifre zaten belirlenmiş")
        if not hmac.compare_digest(body.token, st.setup_token):
            raise HTTPException(403, "Kurulum anahtarı hatalı (konsolda yazan adresi kullanın)")
        write_private(st.auth_path, json.dumps(hash_password(body.password)))
        st.setup_token = None
        resp = JSONResponse({"ok": True})
        new_session(resp)
        return resp

    @app.post("/api/auth/login")
    async def login(body: LoginBody):
        if st.setup_token is not None:
            raise HTTPException(409, "Önce şifre belirleyin")
        if not check_password(body.password):
            raise HTTPException(401, "Şifre hatalı")
        unlock_vault(body.password)
        resp = JSONResponse({"ok": True})
        new_session(resp)
        return resp

    @app.post("/api/auth/logout")
    async def logout(request: Request, s: dict = Depends(session)):
        st.sessions.pop(request.cookies.get("tsa_session", ""), None)
        resp = JSONResponse({"ok": True})
        resp.delete_cookie("tsa_session", path="/")
        return resp

    @app.get("/api/auth/csrf")
    async def csrf(s: dict = Depends(session)):
        return {"csrf": s["csrf"]}

    # ------------------------------------------------------------ keys (write-only)
    @app.get("/api/keys")
    async def keys_state(s: dict = Depends(session)):
        return {"stored": st.vault.exists(), "source": st.keys_source, "unlocked": st.keys is not None,
                "check": st.last_check}

    @app.put("/api/keys")
    async def keys_put(body: KeysBody, s: dict = Depends(session)):
        if not check_password(body.password):
            raise HTTPException(401, "Şifre hatalı")
        if not (KEY_RE.match(body.api_key) and KEY_RE.match(body.api_secret)):
            raise HTTPException(422, "API anahtarı / gizli anahtar biçimi geçersiz")
        if st.engine and st.engine.running and st.engine.mode in ("live", "testnet"):
            raise HTTPException(409, "Önce botu durdurun")
        st.vault.save(body.password, body.api_key, body.api_secret)
        st.keys, st.keys_source = (Secret(body.api_key), Secret(body.api_secret)), "vault"
        REDACT.add(*st.keys)
        st.last_check = {}
        return {"stored": True}

    @app.delete("/api/keys")
    async def keys_delete(s: dict = Depends(session)):
        if st.engine and st.engine.running and st.engine.mode in ("live", "testnet"):
            raise HTTPException(409, "Önce botu durdurun")
        st.vault.delete()
        if st.keys_source == "vault":
            st.keys, st.keys_source = None, None
        st.last_check = {}
        return {"stored": False}

    async def permission_check(venue: str) -> dict:
        if not st.keys:
            return {"ok": False, "error": "API anahtarı yok"}
        client = st.client_for(venue)
        try:
            out = {"venue": venue, "ts": int(time.time() * 1000)}
            await client.sync_time()
            acc = await client.account()
            out["available_usdt"] = float(acc.get("availableBalance", 0))
            out["dual_side"] = await client.dual_side()
            if venue == "live":
                r = await client.api_restrictions()
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

    @app.post("/api/keys/check")
    async def keys_check(body: CheckBody, s: dict = Depends(session)):
        st.last_check = await permission_check(body.venue)
        return st.last_check

    # ------------------------------------------------------------ settings
    @app.get("/api/settings")
    async def get_settings(s: dict = Depends(session)):
        return asdict(st.settings)

    @app.put("/api/settings")
    async def put_settings(request: Request, s: dict = Depends(session)):
        try:
            d = await request.json()
        except Exception:
            raise HTTPException(400, "Geçersiz JSON")
        if not isinstance(d, dict):
            raise HTTPException(400, "Geçersiz JSON")
        new = Settings.from_dict({**asdict(st.settings), **d})
        err = new.validate()
        if err:
            raise HTTPException(422, "; ".join(err))
        if st.engine and st.engine.running:
            raise HTTPException(409, "Ayarları değiştirmek için önce botu durdurun")
        if new.mode != "live":
            new.live_confirmed = False if st.settings.mode == "live" else new.live_confirmed
        st.settings = new
        save_settings(new, st.settings_path)
        return asdict(new)

    # ------------------------------------------------------------ bot control
    @app.post("/api/bot/start")
    async def bot_start(s: dict = Depends(session)):
        cfg = st.settings
        if st.engine and st.engine.running:
            return {"ok": True, "running": True}
        if cfg.mode in ("testnet", "live") and not st.keys:
            raise HTTPException(400, "Bu mod için API anahtarı gerekli (API Anahtarları sekmesi)")
        if cfg.mode == "live":
            if not cfg.live_confirmed:
                raise HTTPException(400, "Canlı işlem için Ayarlar'da 'Gerçek parayla işlem yapılacağını "
                                         "onaylıyorum' kutusunu işaretleyin")
            chk = await permission_check("live")
            st.last_check = chk
            if not chk.get("ok"):
                raise HTTPException(400, chk.get("error", "Anahtar kontrolü başarısız"))
        if not st.model_path.exists():
            raise HTTPException(500, "Model dosyası bulunamadı")
        st.engine = st.build_engine()
        await st.engine.start()
        return {"ok": True, "running": True}

    @app.post("/api/bot/stop")
    async def bot_stop(s: dict = Depends(session)):
        if st.engine:
            await st.engine.stop()
        return {"ok": True, "running": False}

    @app.post("/api/bot/panic")
    async def bot_panic(s: dict = Depends(session)):
        if st.engine:
            await st.engine.panic()
        return {"ok": True, "running": False}

    @app.post("/api/bot/reset")
    async def bot_reset(s: dict = Depends(session)):
        if st.settings.mode in ("live", "testnet"):
            raise HTTPException(400, "Gerçek/testnet işlem geçmişi silinemez")
        if st.engine and st.engine.running:
            raise HTTPException(409, "Önce botu durdurun")
        st.store.reset_mode(st.settings.mode)
        st.engine = None
        return {"ok": True}

    # ------------------------------------------------------------ data
    def mode() -> str:
        return st.engine.mode if st.engine else st.settings.mode

    @app.get("/api/status")
    async def status(s: dict = Depends(session)):
        if st.engine:
            snap = st.engine.snapshot()
        else:
            m = st.settings.mode
            tot = st.store.realized(m)
            snap = {"running": False, "status": "Durduruldu", "mode": m, "budget": st.settings.budget_usdt,
                    "effective_budget": st.settings.budget_usdt + min(tot, 0), "margin_used": 0.0,
                    "realized": tot, "realized_today": 0.0, "unrealized": 0.0, "positions": [],
                    "last_bar": None, "symbols": len(st.settings.symbols), "stats": st.store.stats(m)}
        snap["keys"] = {"unlocked": st.keys is not None, "source": st.keys_source}
        return snap

    @app.get("/api/trades")
    async def trades(limit: int = 200, s: dict = Depends(session)):
        return st.store.trades(mode(), max(1, min(limit, 1000)))

    @app.get("/api/equity")
    async def equity(s: dict = Depends(session)):
        return st.store.equity(mode())

    @app.get("/api/events")
    async def events(s: dict = Depends(session)):
        return st.store.events(150)

    @app.get("/api/signals")
    async def signals(s: dict = Depends(session)):
        return sorted(st.engine.signals.values(), key=lambda x: (-abs(x["direction"]), x["symbol"])) \
            if st.engine else []

    @app.get("/api/model")
    async def model(s: dict = Depends(session)):
        return Strategy.load(st.model_path).describe() if st.model_path.exists() else {}

    return app
