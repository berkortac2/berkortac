"""Telegram remote: reports, trade history and start / stop / close commands from the phone.

Security
* only api.telegram.org and an explicit list of Bot API methods can be called (no files, no webhooks set)
* the bot answers exactly one chat: the one paired with a 6-digit code shown in the app (10 min, 10 tries,
  private chats only); every other message is ignored without an answer
* the token is stored in the encrypted vault, never returned by the app's API and removed from all logs
* commands older than 2 minutes are ignored and the update offset is saved, so a restart can never
  replay an old /hepsinikapat; closing positions always asks for /onay first
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
import secrets as _secrets
import time
from collections import deque
from dataclasses import dataclass

import httpx

from . import reports
from .secrets import REDACT, Secret

log = logging.getLogger("tsabot.telegram")

API_BASE = "https://api.telegram.org"
METHODS = {"getMe", "getUpdates", "sendMessage", "setMyCommands", "deleteWebhook"}
TOKEN_RE = re.compile(r"[0-9]{6,12}:[A-Za-z0-9_-]{30,60}")
TR = str.maketrans("çğıöşüÇĞİÖŞÜâîûÂÎÛ", "cgiosuCGIOSUaiuAIU")
MAX_AGE_S = 120                     # older commands are ignored (e.g. queued while the app was off)
CONFIRM_S = 60
PAIR_S = 600
PAIR_TRIES = 10

ALIASES = {
    "start": "yardim", "help": "yardim", "yardim": "yardim", "komutlar": "yardim", "menu": "yardim",
    "rapor": "rapor", "durum": "rapor", "status": "rapor", "kasa": "rapor",
    "pozisyonlar": "pozisyonlar", "pozisyon": "pozisyonlar", "acik": "pozisyonlar", "poz": "pozisyonlar",
    "gecmis": "gecmis", "islemler": "gecmis", "history": "gecmis", "bugun": "gecmis",
    "baslat": "baslat", "calistir": "baslat", "run": "baslat",
    "durdur": "durdur", "stop": "durdur", "dur": "durdur",
    "kapat": "kapat", "close": "kapat",
    "hepsinikapat": "hepsinikapat", "tumunukapat": "hepsinikapat", "kapathepsi": "hepsinikapat",
    "kapat_hepsi": "hepsinikapat", "hepsini_kapat": "hepsinikapat", "closeall": "hepsinikapat",
    "onay": "onay", "evet": "onay", "onayla": "onay", "iptal": "iptal", "hayir": "iptal", "vazgec": "iptal",
    "eslestir": "eslestir",
}
CONTROL = {"baslat", "durdur", "kapat", "hepsinikapat", "onay"}
MENU = [("rapor", "Açık pozisyonlar, anlık K/Z, bugünkü kasa"), ("pozisyonlar", "Açık pozisyonlar"),
        ("gecmis", "Bugünkü işlem geçmişi (/gecmis 7)"), ("baslat", "Botu başlat"),
        ("durdur", "Yeni işlem açmayı durdur"), ("kapat", "Bir pozisyonu kapat: /kapat SOLUSDT"),
        ("hepsinikapat", "Botun tüm pozisyonlarını kapat"), ("yardim", "Komut listesi")]


def valid_token(tok: str) -> bool:
    return isinstance(tok, str) and TOKEN_RE.fullmatch(tok) is not None


def parse(text: str) -> tuple[str | None, list[str]]:
    """'/Başlat@TsaBot  x' -> ('baslat', ['x']); Turkish letters and case do not matter."""
    t = (text or "").strip()
    if not t.startswith("/"):
        return None, []
    parts = t.split()
    cmd = parts[0][1:].split("@", 1)[0].translate(TR).lower()
    return cmd, parts[1:]


def norm_symbol(x: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]", "", x.translate(TR)).upper()
    return s if s.endswith("USDT") else s + "USDT"


class TelegramError(Exception):
    def __init__(self, status: int, desc: str, retry_after: int | None = None):
        super().__init__(f"Telegram {status}: {desc}")
        self.status, self.desc, self.retry_after = status, desc, retry_after


class TelegramAPI:
    def __init__(self, token: Secret, http: httpx.AsyncClient | None = None):
        if not valid_token(token.reveal()):
            raise ValueError("Telegram bot token biçimi geçersiz")
        self._token = token
        REDACT.add(token)
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=10.0), follow_redirects=False,
                                              trust_env=os.environ.get("TSABOT_TRUST_ENV") == "1",
                                              headers={"User-Agent": "tsabot/1.1"})

    async def call(self, method: str, _http_timeout: float = 20.0, **params):
        if method not in METHODS:
            raise PermissionError(f"Telegram method {method} is not allowed")
        url = f"{API_BASE}/bot{self._token.reveal()}/{method}"
        try:
            r = await self.http.post(url, json=params, timeout=_http_timeout)
        except httpx.TransportError as e:        # the message may contain the URL (token): never chain it
            raise TelegramError(0, f"bağlantı hatası ({type(e).__name__})") from None
        try:
            j = r.json()
        except ValueError:
            raise TelegramError(r.status_code, "geçersiz yanıt") from None
        if not j.get("ok"):
            raise TelegramError(r.status_code, REDACT.clean(str(j.get("description", "")))[:200],
                                (j.get("parameters") or {}).get("retry_after"))
        return j["result"]

    async def close(self):
        await self.http.aclose()


@dataclass
class Pairing:
    code: str
    expires: float
    fails: int = 0


class TelegramService:
    def __init__(self, ctl, api_factory=TelegramAPI, clock=time.time):
        self.ctl = ctl
        self.api_factory = api_factory
        self.clock = clock
        self.api = None
        self.task: asyncio.Task | None = None
        self.me: dict | None = None
        self.status = "off"                 # off | connecting | ok | error
        self.last_error = ""
        self.pairing: Pairing | None = None
        self.pending: tuple[str, list, float] | None = None       # (action, symbols, expires)
        self.outbox: deque = deque(maxlen=100)
        self.cmd_times: deque = deque(maxlen=30)
        self.throttle: dict[str, float] = {}
        self.summary_day = None
        self.offset = int(ctl.store.get("telegram:offset", 0) or 0)

    @property
    def prefs(self):
        return self.ctl.tg_prefs

    # ------------------------------------------------------------------ lifecycle
    async def start(self, token: Secret) -> None:
        await self.stop()
        self.api = self.api_factory(token)
        self.status, self.last_error, self.me = "connecting", "", None
        self.task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
            self.task = None
        if self.api:
            try:
                await self.api.close()
            except Exception:
                pass
            self.api = None
        self.status = "off"

    async def check_token(self, token: Secret) -> dict:
        api = self.api_factory(token)
        try:
            return await api.call("getMe")
        finally:
            await api.close()

    async def _run(self) -> None:
        backoff = 2.0
        while True:
            try:
                if self.me is None:
                    self.me = await self.api.call("getMe")
                    self.ctl.on_telegram_identity(self.me)
                    await self.api.call("deleteWebhook")          # getUpdates does not work while one is set
                    await self.api.call("setMyCommands", commands=[{"command": c, "description": d} for c, d in MENU])
                upd = await self._get_updates()
                self.status, self.last_error, backoff = "ok", "", 2.0
                for u in upd:
                    self.offset = max(self.offset, int(u.get("update_id", 0)) + 1)
                    self.ctl.store.put("telegram:offset", self.offset)
                    try:
                        await self._handle(u.get("message") or {})
                    except Exception as e:
                        log.warning("telegram command failed: %s", REDACT.clean(str(e))[:200])
                await self._flush()
                await self._daily_tick()
            except asyncio.CancelledError:
                raise
            except TelegramError as e:
                self.status, self.last_error = "error", e.desc
                if e.status in (401, 404):
                    self.last_error = "Token geçersiz ya da bot silinmiş (BotFather'dan yeni token al)"
                    log.warning("telegram: %s", self.last_error)
                    return
                if e.status == 409:
                    self.last_error = "Aynı Telegram botu başka bir programda da çalışıyor (aynı token)"
                wait = e.retry_after or (30 if e.status == 409 else backoff)
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, 60.0)
            except Exception as e:
                self.status, self.last_error = "error", REDACT.clean(f"{type(e).__name__}: {e}")[:200]
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def _get_updates(self) -> list:
        # long polling: Telegram holds the request up to 25 s until a message arrives
        return await self.api.call("getUpdates", _http_timeout=40.0, offset=self.offset, timeout=25,
                                   allowed_updates=["message"])

    # ------------------------------------------------------------------ pairing
    def begin_pairing(self) -> dict:
        self.pairing = Pairing(f"{_secrets.randbelow(10 ** 6):06d}", self.clock() + PAIR_S)
        user = (self.me or {}).get("username") or self.prefs.bot_username
        return {"code": self.pairing.code, "expires": int(self.pairing.expires * 1000),
                "link": f"https://t.me/{user}" if user else None, "bot": user}

    def pairing_info(self) -> dict | None:
        if not self.pairing or self.clock() > self.pairing.expires:
            self.pairing = None
            return None
        return {"code": self.pairing.code, "expires": int(self.pairing.expires * 1000)}

    async def _try_pair(self, msg: dict, text: str) -> None:
        chat = msg.get("chat") or {}
        if not self.pairing or self.clock() > self.pairing.expires or chat.get("type") != "private":
            return
        cmd, args = parse(text)
        code = args[0] if cmd == "eslestir" and args else text.strip() if text.strip().isdigit() else None
        if code is None:
            return
        if not hmac.compare_digest(code.encode(), self.pairing.code.encode()):
            self.pairing.fails += 1
            if self.pairing.fails >= PAIR_TRIES:
                self.pairing = None                      # brute force protection
            return
        self.pairing = None
        frm = msg.get("from") or {}
        name = frm.get("username") and "@" + frm["username"] or " ".join(
            x for x in (frm.get("first_name"), frm.get("last_name")) if x) or str(chat.get("id"))
        self.ctl.on_telegram_paired(int(chat["id"]), name[:64])
        await self.send("✅ <b>Eşleşme tamam.</b> Bu sohbet artık TSA Bot'a bağlı; bot yalnızca bu sohbete cevap verir.\n\n"
                        + reports.HELP)

    # ------------------------------------------------------------------ incoming
    async def _handle(self, msg: dict) -> None:
        chat = msg.get("chat") or {}
        text = msg.get("text") or ""
        cid = chat.get("id")
        if not text or cid is None:
            return
        if self.prefs.chat_id is None or cid != self.prefs.chat_id:
            await self._try_pair(msg, text)
            return
        if self.clock() - float(msg.get("date") or 0) > MAX_AGE_S:
            return                                          # stale (sent while the app was closed)
        now = self.clock()
        while self.cmd_times and self.cmd_times[0] < now - 60:
            self.cmd_times.popleft()
        if len(self.cmd_times) >= 20:
            return
        self.cmd_times.append(now)
        await self.send(await self.command(text))

    async def command(self, text: str) -> str:
        cmd, args = parse(text)
        if cmd is None:
            return "Komutlar için /yardim yaz."
        name = ALIASES.get(cmd)
        if name is None:
            return f"Bilinmeyen komut: /{reports.escape(cmd[:30])}\nKomutlar için /yardim yaz."
        if name in CONTROL and not self.prefs.allow_control:
            return "Telefondan kontrol uygulama ayarlarında kapalı (Telegram → Uzaktan kontrol)."
        ctl = self.ctl
        now = ctl.now_ms()
        if name == "yardim":
            return reports.HELP
        if name == "eslestir":
            return "Bu sohbet zaten eşleşmiş."
        if name == "rapor":
            return reports.report(ctl.snapshot(), now, ctl.trade_summary("today"))
        if name == "pozisyonlar":
            return reports.positions(ctl.snapshot(), now)
        if name == "gecmis":
            days = 1
            if args and args[0].isdigit():
                days = max(1, min(int(args[0]), 90))
            rng = "today" if days == 1 else f"{days}d"
            trades = ctl.trades_in(rng, limit=200)
            title = "Bugünkü işlemler" if days == 1 else f"Son {days} günün işlemleri"
            return reports.history(trades, ctl.trade_summary(rng), title)
        if name == "baslat":
            try:
                return await ctl.remote_start("telegram")
            except Exception as e:
                return "⚠️ Başlatılamadı: " + reports.escape(str(getattr(e, "msg", e)))
        if name == "durdur":
            return await ctl.remote_stop()
        if name == "kapat":
            snap = ctl.snapshot()
            open_syms = [p["symbol"] for p in snap.get("positions") or []]
            if not args:
                if not open_syms:
                    return "Botun açık pozisyonu yok."
                return "Hangi pozisyon? Örnek: /kapat " + open_syms[0] + "\nAçık: " + ", ".join(open_syms)
            sym = norm_symbol(args[0])
            p = next((x for x in snap.get("positions") or [] if x["symbol"] == sym), None)
            if p is None:
                return f"Botun {reports.escape(sym)} pozisyonu yok." + (" Açık: " + ", ".join(open_syms) if open_syms else "")
            self.pending = ("close", [sym], self.clock() + CONFIRM_S)
            return (f"❓ <b>{sym}</b> {reports.side(p['direction'])} piyasa fiyatından kapatılacak "
                    f"(şu an {reports.money(p.get('unrealized'))}).\nOnay için {CONFIRM_S} sn içinde /onay yaz, vazgeçmek için /iptal.")
        if name == "hepsinikapat":
            snap = ctl.snapshot()
            pos = snap.get("positions") or []
            if not pos:
                return "Botun açık pozisyonu yok."
            u = sum(float(p.get("unrealized") or 0) for p in pos)
            self.pending = ("close", None, self.clock() + CONFIRM_S)
            return (f"❓ Botun <b>{len(pos)} pozisyonunun hepsi</b> piyasa fiyatından kapatılacak (şu an toplam "
                    f"{reports.money(u)}). Senin elle açtığın pozisyonlara dokunulmaz.\n"
                    f"Onay için {CONFIRM_S} sn içinde /onay yaz, vazgeçmek için /iptal.")
        if name == "iptal":
            had = self.pending is not None
            self.pending = None
            return "İptal edildi." if had else "Bekleyen bir işlem yok."
        if name == "onay":
            if not self.pending or self.clock() > self.pending[2]:
                self.pending = None
                return "Onay bekleyen bir işlem yok (süresi dolmuş olabilir)."
            _, syms, _ = self.pending
            self.pending = None
            res = await ctl.close_positions(syms, reason="Telegram'dan kapatıldı")
            ok = [s for s, r in res.items() if r == "ok"]
            bad = {s: r for s, r in res.items() if r != "ok"}
            out = f"✅ Kapatıldı: {', '.join(ok)}" if ok else "Kapatılan pozisyon yok."
            if bad:
                out += "\n⚠️ Kapatılamadı: " + "; ".join(f"{s}: {reports.escape(r)}" for s, r in bad.items())
            return out
        return reports.HELP

    # ------------------------------------------------------------------ outgoing
    async def send(self, text: str, chat_id: int | None = None) -> bool:
        cid = chat_id or self.prefs.chat_id
        if cid is None or self.api is None:
            return False
        for part in _chunks(text):
            self.outbox.append((cid, part, self.clock()))
        return await self._flush()

    async def _flush(self) -> bool:
        while self.outbox and self.api is not None:
            cid, text, created = self.outbox[0]
            if self.clock() - created > 6 * 3600:          # too old to be useful
                self.outbox.popleft()
                continue
            try:
                await self.api.call("sendMessage", chat_id=cid, text=text, parse_mode="HTML",
                                    disable_web_page_preview=True)
            except TelegramError as e:
                if e.status == 400:                        # malformed: drop it, never block the queue
                    self.outbox.popleft()
                    log.warning("telegram message dropped: %s", e.desc)
                    continue
                return False                              # network: retried on the next poll
            self.outbox.popleft()
        return True

    def notify(self, kind: str, data: dict) -> None:
        """Engine events -> phone (called inside the engine's event loop; never raises)."""
        if self.prefs.chat_id is None or self.api is None:
            return
        p = self.prefs
        text = None
        if kind == "trade_open" and p.notify_trades:
            text = reports.trade_open(data)
        elif kind == "trade_close" and p.notify_trades:
            text = reports.trade_close(data)
        elif kind == "error" and p.notify_errors:
            key = str(data.get("msg", ""))[:60]
            if self.clock() - self.throttle.get(key, 0) < 1800:
                return
            self.throttle[key] = self.clock()
            text = "⚠️ " + reports.escape(str(data.get("msg", "")))
        elif kind == "stopped" and p.notify_errors:
            text = "⏹ Bot durdu." + (f" {data['positions']} açık pozisyon borsadaki stop emirleriyle korunuyor."
                                     if data.get("positions") else "")
        elif kind == "net_down" and p.notify_errors:
            text = (f"📡 Binance'e {data.get('minutes')} dakikadır ulaşılamıyor. Açık pozisyonları borsadaki "
                    "stop emirleri koruyor; bot bağlantı dönünce kaldığı yerden devam eder.")
        elif kind == "net_up" and p.notify_errors:
            text = f"📶 Bağlantı geri geldi ({data.get('minutes')} dk kesinti)."
        if text:
            for part in _chunks(text):
                self.outbox.append((p.chat_id, part, self.clock()))
            try:
                asyncio.get_running_loop().create_task(self._flush())
            except RuntimeError:
                pass

    async def _daily_tick(self) -> None:
        p = self.prefs
        if not p.daily_summary or p.chat_id is None:
            return
        lt = time.localtime(self.clock())
        day = (lt.tm_year, lt.tm_yday)
        if lt.tm_hour == p.daily_summary_hour and self.summary_day != day:
            self.summary_day = day
            now = self.ctl.now_ms()
            await self.send("🗓 <b>Günlük özet</b>\n\n" + reports.history(self.ctl.trades_in("today", 200),
                                                                          self.ctl.trade_summary("today"),
                                                                          "Bugünkü işlemler")
                            + "\n\n" + reports.positions(self.ctl.snapshot(), now))

    def view(self) -> dict:
        p = self.prefs
        return {"configured": self.ctl.tg_token is not None, "status": self.status, "error": self.last_error,
                "bot_username": (self.me or {}).get("username") or p.bot_username, "paired": p.chat_id is not None,
                "chat_name": p.chat_name, "pairing": self.pairing_info(),
                "prefs": {k: getattr(p, k) for k in ("notify_trades", "notify_errors", "daily_summary",
                                                      "daily_summary_hour", "allow_control")}}


def _chunks(text: str, n: int = 3800) -> list[str]:
    if len(text) <= n:
        return [text]
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > n and cur:
            out.append(cur)
            cur = ""
        cur += ("\n" if cur else "") + line[:n]
    return out + ([cur] if cur else [])
