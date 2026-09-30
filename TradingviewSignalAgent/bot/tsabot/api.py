"""Local web API + UI server (shown in the desktop window, or in a browser at http://127.0.0.1).

Security model
* binds to 127.0.0.1 only; Host header must be localhost/127.0.0.1 (DNS-rebinding guard)
* first run: password set with a one-time setup token (the desktop app passes it to its own window)
* session cookie (HttpOnly, SameSite=Strict) + CSRF header on every state change,
  JSON-only bodies, same-origin check, strict CSP, no CORS
* secrets (Binance key/secret, Telegram token): write-only; stored in the encrypted vault (scrypt + Fernet)
  or read from environment variables; never returned or logged
* live trading refuses keys that can withdraw or transfer funds
"""
from __future__ import annotations

import hmac
import json
import math
import secrets as _secrets
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import BOT_ROOT
from .config import DATA_DIR, MODEL_PATH
from .control import ControlError, Controller
from .reports import report
from .secrets import hash_password, valid_key, verify_password, write_private
from .strategy import Strategy

WEB = BOT_ROOT / "web"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
SESSION_TTL = 12 * 3600          # idle timeout
SESSION_MAX = 24 * 3600          # absolute lifetime
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "font-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


class SetupBody(BaseModel):
    token: str = Field(max_length=128)
    password: str = Field(min_length=10, max_length=256)


class LoginBody(BaseModel):
    password: str = Field(max_length=256)
    remember: bool = False


class DesktopBody(BaseModel):
    token: str = Field(default="", max_length=128)


class RememberBody(BaseModel):
    enable: bool = False


class KeysBody(BaseModel):
    password: str = Field(max_length=256)
    api_key: str = Field(max_length=128)
    api_secret: str = Field(max_length=128)


class CheckBody(BaseModel):
    venue: str = Field(pattern="^(live|testnet)$")


class StartBody(BaseModel):
    password: str = Field(default="", max_length=256)


class StopBody(BaseModel):
    how: str = Field(default="drain", pattern="^(drain|full|close)$")


class CloseBody(BaseModel):
    symbol: str = Field(default="", pattern="^([A-Z0-9]{2,20}USDT)?$")


class TokenBody(BaseModel):
    token: str = Field(default="", max_length=100)


class TgPrefsBody(BaseModel):
    notify_trades: bool | None = None
    notify_errors: bool | None = None
    daily_summary: bool | None = None
    daily_summary_hour: int | None = Field(default=None, ge=0, le=23)
    allow_control: bool | None = None


class AppPrefsBody(BaseModel):
    autostart: bool | None = None
    resume_bot: bool | None = None
    notify_desktop: bool | None = None
    prevent_sleep: bool | None = None


def _clean(x):
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return x


class SafeJSON(JSONResponse):
    """JSON without NaN / Infinity (not valid JSON; e.g. the model statistics contain NaN)."""

    def render(self, content) -> bytes:
        return json.dumps(_clean(content), ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def ceq(a: str, b: str) -> bool:
    """Constant-time compare that never raises on non-ASCII input."""
    return hmac.compare_digest((a or "").encode(), (b or "").encode())


AppState = Controller      # old name, still used by tests and the desktop shell (app.state.tsa)


def create_app(data_dir: Path | None = None, model_path: Path = MODEL_PATH, factories: dict | None = None,
               protector=None, desktop: bool = False) -> FastAPI:
    st = Controller(data_dir or DATA_DIR, model_path, factories, protector=protector, desktop=desktop)

    @asynccontextmanager
    async def lifespan(_app):
        await st.on_startup()
        yield
        await st.on_shutdown()

    app = FastAPI(title="TSA Bot", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan,
                  default_response_class=SafeJSON)
    app.state.tsa = st

    def fail(e: ControlError):
        raise HTTPException(e.status, e.msg)

    # ------------------------------------------------------------ middleware
    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in ALLOWED_HOSTS:
            return PlainTextResponse("Invalid host", status_code=400)
        if "transfer-encoding" in request.headers:
            return PlainTextResponse("Chunked bodies are not accepted", status_code=411)
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            origin = request.headers.get("origin")
            if origin and origin != f"http://{request.headers.get('host', '')}":
                return PlainTextResponse("Cross-origin request refused", status_code=403)
            if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
                return PlainTextResponse("Cross-site request refused", status_code=403)
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

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        # never echo submitted values (they may be secrets)
        msg = "; ".join(f"{'.'.join(str(x) for x in e.get('loc', ())[1:])}: {e.get('msg', '')}" for e in exc.errors())
        return JSONResponse({"detail": msg[:300] or "Geçersiz istek"}, status_code=422)

    # ------------------------------------------------------------ auth helpers
    def session(request: Request) -> dict:
        tok = request.cookies.get("tsa_session", "")
        s = st.sessions.get(tok)
        if not s or s["exp"] < time.time() or s["created"] + SESSION_MAX < time.time():
            st.sessions.pop(tok, None)
            raise HTTPException(401, "Oturum gerekli")
        if request.method != "GET":
            if not ceq(request.headers.get("x-csrf-token", ""), s["csrf"]):
                raise HTTPException(403, "CSRF doğrulaması başarısız")
        s["exp"] = time.time() + SESSION_TTL
        return s

    def new_session(resp: JSONResponse) -> None:
        now = time.time()
        for k in [k for k, v in st.sessions.items() if v["exp"] < now or v["created"] + SESSION_MAX < now]:
            st.sessions.pop(k, None)
        tok, csrf = _secrets.token_urlsafe(32), _secrets.token_urlsafe(32)
        st.sessions[tok] = {"exp": now + SESSION_TTL, "created": now, "csrf": csrf}
        resp.set_cookie("tsa_session", tok, httponly=True, samesite="strict", max_age=SESSION_TTL, path="/")
        resp.headers["X-CSRF-Token"] = csrf

    def check_password(pw: str) -> bool:
        now = time.time()
        if now < st.locked_until:
            raise HTTPException(429, f"Çok fazla hatalı deneme. {int(st.locked_until - now) + 1} sn bekleyin.")
        ok = st.auth_path.exists() and verify_password(pw, json.loads(st.auth_path.read_text(encoding="utf-8")))
        if ok:
            st.fail_count = 0
            return True
        st.fail_count += 1
        if st.fail_count >= 5:
            st.locked_until = now + min(900, 30 * 2 ** (st.fail_count - 5))
        return False

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
        return {"setup_required": st.setup_token is not None, "logged_in": bool(s and s["exp"] > time.time()),
                "desktop": st.desktop, "remember_available": st.protector.available}

    @app.post("/api/auth/setup")
    async def setup(body: SetupBody):
        if st.setup_token is None:
            raise HTTPException(409, "Şifre zaten belirlenmiş")
        if not ceq(body.token, st.setup_token):
            raise HTTPException(403, "Kurulum anahtarı hatalı (konsolda yazan adresi kullanın)")
        write_private(st.auth_path, json.dumps(hash_password(body.password)))
        st.setup_token = None
        if st.vault.exists():                 # left over from an earlier installation: its password is unknown
            st.vault.delete()
        st.unlock(body.password)
        resp = JSONResponse({"ok": True})
        new_session(resp)
        return resp

    @app.post("/api/auth/login")
    async def login(body: LoginBody):
        if st.setup_token is not None:
            raise HTTPException(409, "Önce şifre belirleyin")
        if not check_password(body.password):
            raise HTTPException(401, "Şifre hatalı")
        st.unlock(body.password)
        if body.remember and st.protector.available:
            st.set_remember(True)
        resp = JSONResponse({"ok": True})
        new_session(resp)
        return resp

    @app.post("/api/auth/desktop")
    async def desktop_login(body: DesktopBody):
        """The desktop window opens with a per-process token. It logs in only when 'remember me'
        (Windows DPAPI) or an earlier login in this process has unlocked the vault."""
        if not (st.desktop and st.desktop_token and ceq(body.token, st.desktop_token)):
            raise HTTPException(403, "Geçersiz masaüstü anahtarı")
        if st.setup_token is not None:
            raise HTTPException(409, "Önce şifre belirleyin")
        if st.dek is None and not st.auto_unlock():
            raise HTTPException(401, "Giriş gerekli")
        resp = JSONResponse({"ok": True})
        new_session(resp)
        return resp

    @app.post("/api/auth/remember")
    async def remember(body: RememberBody, s: dict = Depends(session)):
        if body.enable and not st.protector.available:
            raise HTTPException(400, "Bu özellik yalnızca Windows'ta var")
        return {"remembered": st.set_remember(body.enable)}

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
        return {"stored": st.vault.has("binance"), "source": st.keys_source, "unlocked": st.keys is not None,
                "check": st.last_check}

    @app.put("/api/keys")
    async def keys_put(body: KeysBody, s: dict = Depends(session)):
        async with st.lock:
            if not check_password(body.password):
                raise HTTPException(401, "Şifre hatalı")
            if not (valid_key(body.api_key) and valid_key(body.api_secret)):
                raise HTTPException(422, "API anahtarı / gizli anahtar biçimi geçersiz")
            if st.engine and st.engine.running and st.engine.mode in ("live", "testnet"):
                raise HTTPException(409, "Önce botu durdurun")
            try:
                st.save_keys(body.password, body.api_key, body.api_secret)
            except ControlError as e:
                fail(e)
            return {"stored": True}

    @app.delete("/api/keys")
    async def keys_delete(s: dict = Depends(session)):
        if st.engine and st.engine.running and st.engine.mode in ("live", "testnet"):
            raise HTTPException(409, "Önce botu durdurun")
        st.delete_keys()
        return {"stored": False}

    @app.post("/api/keys/check")
    async def keys_check(body: CheckBody, s: dict = Depends(session)):
        st.last_check = await st.permission_check(body.venue)
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
        d.pop("budget_set_at", None)                    # set by the server only
        try:
            return asdict(await st.update_settings(d))
        except ControlError as e:
            fail(e)

    @app.get("/api/app")
    async def app_info(s: dict = Depends(session)):
        return {**st.app_info(), "prefs": asdict(st.app_prefs)}

    @app.put("/api/app/prefs")
    async def app_prefs(body: AppPrefsBody, s: dict = Depends(session)):
        d = {k: v for k, v in body.model_dump().items() if v is not None}
        if d.get("autostart") and not st.desktop:
            raise HTTPException(400, "Windows ile başlatma yalnızca masaüstü uygulamasında ayarlanabilir")
        return asdict(st.save_app_prefs(d))

    # ------------------------------------------------------------ bot control
    @app.post("/api/bot/start")
    async def bot_start(body: StartBody, s: dict = Depends(session)):
        # real money from the UI: the password is asked again (not needed to re-enable a running bot)
        resuming = st.engine is not None and st.engine.running
        pw_ok = st.settings.mode != "live" or resuming or check_password(body.password)
        try:
            return await st.start_bot("ui", password_ok=pw_ok)
        except ControlError as e:
            fail(e)

    @app.post("/api/bot/stop")
    async def bot_stop(body: StopBody, s: dict = Depends(session)):
        try:
            return await st.stop_bot(body.how)
        except ControlError as e:
            fail(e)

    @app.post("/api/bot/panic")
    async def bot_panic(s: dict = Depends(session)):
        try:
            return await st.panic()
        except ControlError as e:
            fail(e)

    @app.post("/api/bot/reset")
    async def bot_reset(s: dict = Depends(session)):
        if st.settings.mode in ("live", "testnet"):
            raise HTTPException(400, "Gerçek/testnet işlem geçmişi silinemez")
        if st.engine and st.engine.running:
            raise HTTPException(409, "Önce botu durdurun")
        st.store.reset_mode(st.settings.mode)
        st.engine = None
        return {"ok": True}

    @app.post("/api/positions/close")
    async def close_one(body: CloseBody, s: dict = Depends(session)):
        if not body.symbol:
            raise HTTPException(422, "symbol gerekli")
        try:
            r = await st.close_positions([body.symbol])
        except ControlError as e:
            fail(e)
        if r.get(body.symbol) != "ok":
            raise HTTPException(400, r.get(body.symbol) or "Kapatılamadı")
        return {"ok": True, "result": r}

    @app.post("/api/positions/close-all")
    async def close_all(s: dict = Depends(session)):
        try:
            r = await st.close_positions(None)
        except ControlError as e:
            fail(e)
        return {"ok": all(v == "ok" for v in r.values()), "result": r, "closed": sum(v == "ok" for v in r.values())}

    # ------------------------------------------------------------ data
    @app.get("/api/status")
    async def status(s: dict = Depends(session)):
        return st.snapshot()

    @app.get("/api/wallet")
    async def wallet(force: int = 0, s: dict = Depends(session)):
        return await st.wallet(bool(force))

    @app.get("/api/trades")
    async def trades(limit: int = 200, range: str = "all", s: dict = Depends(session)):
        try:
            return st.trades_in(range, max(1, min(limit, 5000)))
        except ControlError as e:
            fail(e)

    @app.get("/api/trades/summary")
    async def trades_summary(s: dict = Depends(session)):
        return {r: st.trade_summary(r) for r in ("today", "7d", "30d", "all")}

    @app.get("/api/equity")
    async def equity(range: str = "7d", s: dict = Depends(session)):
        try:
            return st.equity(range)
        except ControlError as e:
            fail(e)

    @app.get("/api/pnl/daily")
    async def pnl_daily(days: int = 30, s: dict = Depends(session)):
        return st.daily(max(1, min(days, 365)))

    @app.get("/api/events")
    async def events(s: dict = Depends(session)):
        return st.store.events(150)

    @app.get("/api/signals")
    async def signals(s: dict = Depends(session)):
        return sorted(st.engine.signals.values(), key=lambda x: (-abs(x["direction"]), x["symbol"])) \
            if st.engine else []

    @app.get("/api/model")
    async def model(s: dict = Depends(session)):
        if not st.model_path.exists():
            return {}
        return {**Strategy.load(st.model_path).describe(), "emergency_stop_pct": st.settings.emergency_stop_pct}

    # ------------------------------------------------------------ telegram
    @app.get("/api/telegram")
    async def tg_view(s: dict = Depends(session)):
        return st.telegram.view()

    @app.put("/api/telegram/token")
    async def tg_token(body: TokenBody, s: dict = Depends(session)):
        try:
            return await st.set_telegram_token(body.token.strip())
        except ControlError as e:
            fail(e)

    @app.delete("/api/telegram")
    async def tg_delete(s: dict = Depends(session)):
        await st.delete_telegram()
        return st.telegram.view()

    @app.post("/api/telegram/pair")
    async def tg_pair(s: dict = Depends(session)):
        if st.tg_token is None:
            raise HTTPException(400, "Önce bot token'ını kaydedin")
        await st.ensure_telegram()
        return st.telegram.begin_pairing()

    @app.post("/api/telegram/unpair")
    async def tg_unpair(s: dict = Depends(session)):
        st.tg_prefs.chat_id, st.tg_prefs.chat_name = None, ""
        st.save_tg_prefs()
        return st.telegram.view()

    @app.post("/api/telegram/test")
    async def tg_test(s: dict = Depends(session)):
        if st.tg_prefs.chat_id is None:
            raise HTTPException(400, "Önce telefonunla eşleştirin")
        ok = await st.telegram.send("🧪 <b>Test mesajı</b>\n\n" + report(st.snapshot(), st.now_ms(),
                                                                        st.trade_summary("today")))
        if not ok:
            raise HTTPException(502, "Mesaj gönderilemedi: " + (st.telegram.last_error or "bağlantı yok"))
        return {"ok": True}

    @app.put("/api/telegram/prefs")
    async def tg_prefs(body: TgPrefsBody, s: dict = Depends(session)):
        for k, v in body.model_dump().items():
            if v is not None:
                setattr(st.tg_prefs, k, v)
        st.save_tg_prefs()
        return st.telegram.view()

    return app
