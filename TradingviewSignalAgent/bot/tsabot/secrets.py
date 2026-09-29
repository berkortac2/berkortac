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
    with os.fdopen(fd, "w") as f:
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
    """API key + secret encrypted with a key derived (scrypt) from the UI password."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def save(self, password: str, api_key: str, api_secret: str) -> None:
        salt = _secrets.token_bytes(16)
        f = Fernet(base64.urlsafe_b64encode(_kdf(password, salt)))
        token = f.encrypt(json.dumps({"k": api_key, "s": api_secret}).encode())
        write_private(self.path, json.dumps({"v": 1, "salt": base64.b64encode(salt).decode(),
                                             "token": token.decode()}))

    def load(self, password: str) -> tuple[Secret, Secret] | None:
        if not self.exists():
            return None
        rec = json.loads(self.path.read_text())
        f = Fernet(base64.urlsafe_b64encode(_kdf(password, base64.b64decode(rec["salt"]))))
        try:
            d = json.loads(f.decrypt(rec["token"].encode()))
        except InvalidToken:
            return None
        return Secret(d["k"]), Secret(d["s"])

    def delete(self) -> None:
        if self.exists():
            self.path.unlink()


def env_keys() -> tuple[Secret, Secret] | None:
    k, s = os.environ.get("BINANCE_API_KEY", ""), os.environ.get("BINANCE_API_SECRET", "")
    return (Secret(k), Secret(s)) if k and s else None


# ------------------------------------------------------------------ log redaction
_PATTERNS = [re.compile(r"(signature=)[0-9a-fA-F]+"), re.compile(r"(X-MBX-APIKEY['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9]+"),
             re.compile(r"(api_?secret['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+", re.I),
             re.compile(r"(api_?key['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+", re.I)]


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
        record.msg, record.args = self.clean(msg), ()
        return True


REDACT = RedactFilter()


def install_redaction() -> None:
    root = logging.getLogger()
    for h in root.handlers:
        h.addFilter(REDACT)
    root.addFilter(REDACT)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore", "tsabot"):
        logging.getLogger(name).addFilter(REDACT)
