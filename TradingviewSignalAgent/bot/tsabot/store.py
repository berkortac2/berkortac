"""SQLite persistence: trades, events, equity curve, open positions."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        if self.path != ":memory:":
            os.chmod(self.path, 0o600)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY, mode TEXT, symbol TEXT, direction INTEGER,
              qty REAL, entry_time INTEGER, entry_price REAL, exit_time INTEGER, exit_price REAL, reason TEXT,
              fees REAL, pnl REAL, margin REAL, rule INTEGER);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts INTEGER, level TEXT, msg TEXT);
            CREATE TABLE IF NOT EXISTS equity(ts INTEGER, mode TEXT, equity REAL, realized REAL);
            CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
            """)
            self.db.commit()

    def _x(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    TRADE_COLS = ("mode", "symbol", "direction", "qty", "entry_time", "entry_price", "exit_time", "exit_price",
                  "reason", "fees", "pnl", "margin", "rule")

    def add_trade(self, t: dict) -> None:
        self._x("INSERT INTO trades(mode, symbol, direction, qty, entry_time, entry_price, exit_time, exit_price, "
                "reason, fees, pnl, margin, rule) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", [t[c] for c in self.TRADE_COLS])

    def trades(self, mode: str, limit: int = 200) -> list[dict]:
        cur = self._x("SELECT * FROM trades WHERE mode=? ORDER BY exit_time DESC LIMIT ?", (mode, limit))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    def realized(self, mode: str, since_ms: int = 0) -> float:
        r = self._x("SELECT COALESCE(SUM(pnl),0) FROM trades WHERE mode=? AND exit_time>=?", (mode, since_ms))
        return float(r.fetchone()[0])

    def stats(self, mode: str) -> dict:
        r = self._x("SELECT COUNT(*), COALESCE(SUM(pnl>0),0), COALESCE(SUM(pnl),0), COALESCE(SUM(fees),0) "
                    "FROM trades WHERE mode=?", (mode,)).fetchone()
        n = int(r[0])
        return {"trades": n, "wins": int(r[1]), "win_rate": (r[1] / n) if n else None, "net_pnl": float(r[2]),
                "fees": float(r[3])}

    def event(self, level: str, msg: str, ts: int | None = None) -> None:
        self._x("INSERT INTO events(ts, level, msg) VALUES(?,?,?)", (ts or int(time.time() * 1000), level, msg[:500]))

    def events(self, limit: int = 100) -> list[dict]:
        cur = self._x("SELECT ts, level, msg FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return [{"ts": a, "level": b, "msg": c} for a, b, c in cur.fetchall()]

    def add_equity(self, ts: int, mode: str, equity: float, realized: float) -> None:
        self._x("INSERT INTO equity(ts, mode, equity, realized) VALUES(?,?,?,?)", (ts, mode, equity, realized))

    def equity(self, mode: str, limit: int = 2000) -> list[dict]:
        cur = self._x("SELECT ts, equity, realized FROM (SELECT * FROM equity WHERE mode=? ORDER BY ts DESC LIMIT ?) "
                      "ORDER BY ts", (mode, limit))
        return [{"ts": a, "equity": b, "realized": c} for a, b, c in cur.fetchall()]

    def put(self, k: str, v) -> None:
        self._x("INSERT OR REPLACE INTO kv(k, v) VALUES(?,?)", (k, json.dumps(v)))

    def get(self, k: str, default=None):
        r = self._x("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else default

    def reset_mode(self, mode: str) -> None:
        self._x("DELETE FROM trades WHERE mode=?", (mode,))
        self._x("DELETE FROM equity WHERE mode=?", (mode,))
        self._x("DELETE FROM kv WHERE k=?", (f"positions:{mode}",))
