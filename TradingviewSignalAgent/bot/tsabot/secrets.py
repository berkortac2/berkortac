"""Secret handling: in-memory wrapper, encrypted vault, password hashing, log redaction."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets as _secrets
import sys
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

SCRYPT = dict(n=2 ** 15, r=8, p=1, maxmem=128 * 1024 * 1024, dklen=32)


class Secret:
    """Holds a sensitive string; repr/str never show it."""
    __slots__ = ("_v",)

    def __init__(self, value: str):
        self._v = value

    def reveal(self) -> str:
        return self._v

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__

    def __bool__(self) -> bool:
        return bool(self._v)


def _kdf(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)


def write_private(path: Path, data: str) -> None:
    """Write a file readable only by the owner (0600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


# ------------------------------------------------------------------ password
def hash_password(password: str) -> dict:
    salt = _secrets.token_bytes(16)
    return {"salt": base64.b64encode(salt).decode(), "hash": base64.b64encode(_kdf(password, salt)).decode()}


def verify_password(password: str, rec: dict) -> bool:
    salt = base64.b64decode(rec["salt"])
    return hmac.compare_digest(_kdf(password, salt), base64.b64decode(rec["hash"]))


# ------------------------------------------------------------------ vault
class Vault:
    """Encrypted secrets (Binance API key/secret, Telegram bot token).

    v2 layout: a random data key (DEK) encrypts every item; the DEK itself is stored only encrypted with a
    key derived from the UI password (scrypt). After a login the DEK is kept in memory, so the Telegram token
    can be stored without asking the password again. v1 files (key+secret encrypted directly with the
    password key) are migrated on the first unlock."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def _read(self) -> dict | None:
        if not self.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write(self, rec: dict) -> None:
        write_private(self.path, json.dumps(rec))

    @staticmethod
    def _kek(password: str, salt: bytes) -> Fernet:
        return Fernet(base64.urlsafe_b64encode(_kdf(password, salt)))

    def create(self, password: str) -> bytes:
        """New empty vault; returns its data key."""
        salt, dek = _secrets.token_bytes(16), Fernet.generate_key()
        self._write({"v": 2, "salt": base64.b64encode(salt).decode(),
                     "wrapped": self._kek(password, salt).encrypt(dek).decode(), "items": {}})
        return dek

    def unlock(self, password: str) -> bytes | None:
        """Data key for the right password, None otherwise."""
        rec = self._read()
        if rec is None:
            return None
        kek = self._kek(password, base64.b64decode(rec["salt"]))
        try:
            if rec.get("v") == 2:
                return kek.decrypt(rec["wrapped"].encode())
            old = json.loads(kek.decrypt(rec["token"].encode()))       # v1 -> v2
        except (InvalidToken, KeyError, ValueError):
            return None
        dek = self.create(password)
        self.put(dek, "binance", {"k": old["k"], "s": old["s"]})
        return dek

    def rewrap(self, dek: bytes, password: str) -> None:
        """Protect the same data key with a new password (password change)."""
        rec = self._read()
        salt = _secrets.token_bytes(16)
        rec.update(salt=base64.b64encode(salt).decode(), wrapped=self._kek(password, salt).encrypt(dek).decode())
        self._write(rec)

    def put(self, dek: bytes, name: str, value: dict) -> None:
        rec = self._read()
        if rec is None or rec.get("v") != 2:
            raise ValueError("vault not initialised")
        rec.setdefault("items", {})[name] = Fernet(dek).encrypt(json.dumps(value).encode()).decode()
        self._write(rec)

    def get(self, dek: bytes, name: str) -> dict | None:
        rec = self._read()
        tok = (rec or {}).get("items", {}).get(name)
        if not tok:
            return None
        try:
            return json.loads(Fernet(dek).decrypt(tok.encode()))
        except (InvalidToken, ValueError):
            return None

    def has(self, name: str) -> bool:
        rec = self._read() or {}
        return bool(rec.get("items", {}).get(name)) or (name == "binance" and "token" in rec)

    def remove(self, name: str) -> None:
        rec = self._read()
        if rec and rec.get("v") == 2 and name in rec.get("items", {}):
            del rec["items"][name]
            self._write(rec)
        elif rec and rec.get("v") != 2 and name == "binance":
            self.delete()

    # ---- compatibility helpers (API key + secret)
    def save(self, password: str, api_key: str, api_secret: str) -> None:
        dek = self.unlock(password) if self.exists() else self.create(password)
        if dek is None:
            raise ValueError("wrong password")
        self.put(dek, "binance", {"k": api_key, "s": api_secret})

    def load(self, password: str) -> tuple[Secret, Secret] | None:
        dek = self.unlock(password)
        d = self.get(dek, "binance") if dek else None
        return (Secret(d["k"]), Secret(d["s"])) if d else None

    def delete(self) -> None:
        if self.exists():
            self.path.unlink()


# ------------------------------------------------------------------ "remember me" (Windows DPAPI)
class Protector:
    """Encrypts the vault's data key for the current OS user (Windows DPAPI), so the desktop app can
    unlock itself after a reboot. A copy of the file is useless on another computer / Windows account.
    Not available on other systems (available = False)."""
    ENTROPY = b"TSABot remember v1"

    @property
    def available(self) -> bool:
        return sys.platform == "win32"

    def protect(self, data: bytes) -> bytes:
        return _dpapi(data, True, self.ENTROPY)

    def unprotect(self, data: bytes) -> bytes:
        return _dpapi(data, False, self.ENTROPY)


def _dpapi(data: bytes, protect: bool, entropy: bytes) -> bytes:   # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def mk(b: bytes):
        buf = ctypes.create_string_buffer(b, len(b))
        return BLOB(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(BLOB), ctypes.c_void_p, ctypes.POINTER(BLOB), ctypes.c_void_p, ctypes.c_void_p,
                   wintypes.DWORD, ctypes.POINTER(BLOB)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    inp, _k1 = mk(data)
    ent, _k2 = mk(entropy)
    out = BLOB()
    if not fn(ctypes.byref(inp), None, ctypes.byref(ent), None, None, 0x1, ctypes.byref(out)):  # UI_FORBIDDEN
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


KEY_RE = re.compile(r"[A-Za-z0-9]{16,128}")


def valid_key(v: str) -> bool:
    return isinstance(v, str) and KEY_RE.fullmatch(v) is not None


def env_keys() -> tuple[Secret, Secret] | None:
    k = os.environ.get("BINANCE_API_KEY", "").strip()
    s = os.environ.get("BINANCE_API_SECRET", "").strip()
    return (Secret(k), Secret(s)) if valid_key(k) and valid_key(s) else None


# ------------------------------------------------------------------ log redaction
_PATTERNS = [re.compile(r"(signature=)[0-9a-fA-F]+"), re.compile(r"(X-MBX-APIKEY['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9]+"),
             re.compile(r"(api_?secret['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+", re.I),
             re.compile(r"(api_?key['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+", re.I),
             re.compile(r"(api\.telegram\.org/bot)[0-9]+:[A-Za-z0-9_-]+"),
             re.compile(r"(\b)[0-9]{6,12}:[A-Za-z0-9_-]{30,}")]


class RedactFilter(logging.Filter):
    """Removes registered secret values and signature/key patterns from every log record."""

    def __init__(self):
        super().__init__()
        self._values: set[str] = set()

    def add(self, *values: Secret | str) -> None:
        for v in values:
            s = v.reveal() if isinstance(v, Secret) else v
            if s and len(s) >= 6:
                self._values.add(s)

    def clean(self, text: str) -> str:
        for v in self._values:
            text = text.replace(v, "***")
        for p in _PATTERNS:
            text = p.sub(r"\1***", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if record.exc_info:
            msg += "\n" + logging.Formatter().formatException(record.exc_info)
            record.exc_info, record.exc_text = None, None
        if record.stack_info:
            msg += "\n" + record.stack_info
            record.stack_info = None
        record.msg, record.args = self.clean(msg), ()
        return True


REDACT = RedactFilter()


def install_redaction() -> None:
    root = logging.getLogger()
    for h in root.handlers:
        h.addFilter(REDACT)
    root.addFilter(REDACT)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore", "tsabot", "pywebview"):
        logging.getLogger(name).addFilter(REDACT)
