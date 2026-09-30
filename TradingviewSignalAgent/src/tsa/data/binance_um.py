"""Binance USDT-M perpetual futures kline downloader (data.binance.vision).

TradingView's ``BINANCE:<SYM>USDT.P`` charts show exactly these klines, so the
research data is the same data the Pine indicator will see.

fapi.binance.com is geo-blocked from many cloud regions (HTTP 451), so this
module only uses the public data mirror: monthly zips for closed months and
daily zips for the current month.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BASE = "https://data.binance.vision"
S3_LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]
TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}

_session = requests.Session()


def _get(url: str, retries: int = 5, timeout: int = 60) -> requests.Response | None:
    for i in range(retries):
        try:
            r = _session.get(url, timeout=timeout)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r
        except requests.RequestException:
            time.sleep(2 ** i)
    raise RuntimeError(f"download failed: {url}")


def list_prefixes(prefix: str) -> list[str]:
    """List S3 'directories' under a prefix (handles pagination)."""
    out, marker = [], ""
    while True:
        r = _get(f"{S3_LIST}?delimiter=/&prefix={prefix}&marker={marker}")
        txt = r.text
        out += re.findall(r"<Prefix>([^<]*)</Prefix>", txt)[1:]  # first one is the query prefix
        m = re.search(r"<NextMarker>([^<]*)</NextMarker>", txt)
        if not m or "<IsTruncated>true" not in txt:
            break
        marker = m.group(1)
    return out


def list_keys(prefix: str) -> list[str]:
    out, marker = [], ""
    while True:
        r = _get(f"{S3_LIST}?prefix={prefix}&marker={marker}")
        txt = r.text
        keys = re.findall(r"<Key>([^<]*)</Key>", txt)
        out += keys
        if "<IsTruncated>true" not in txt or not keys:
            break
        marker = keys[-1]
    return out


def _parse_zip(content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        name = zf.namelist()[0]
        raw = zf.read(name)
    first = raw[:64].decode(errors="ignore")
    header = 0 if first.startswith("open_time") else None
    df = pd.read_csv(io.BytesIO(raw), header=header, names=None if header == 0 else KLINE_COLS)
    df.columns = KLINE_COLS
    return df


def _fetch_zip(url: str, verify: bool = True) -> pd.DataFrame | None:
    r = _get(url)
    if r is None:
        return None
    if verify:
        c = _get(url + ".CHECKSUM")
        if c is not None:
            expected = c.text.split()[0]
            if hashlib.sha256(r.content).hexdigest() != expected:
                raise RuntimeError(f"checksum mismatch {url}")
    return _parse_zip(r.content)


def _month_range(start: dt.date, end: dt.date) -> list[str]:
    months, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return months


def download_klines(symbol: str, tf: str, start: str, out_dir: Path, today: dt.date | None = None,
                    workers: int = 8) -> pd.DataFrame:
    """Download klines for [start, today) and cache as parquet. Incremental."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{symbol}_{tf}.parquet"
    cached = pd.read_parquet(path) if path.exists() else None

    start_d = dt.date.fromisoformat(start)
    first_of_month = today.replace(day=1)
    last_closed_month = first_of_month - dt.timedelta(days=1)
    months = _month_range(start_d, last_closed_month)
    days = [first_of_month + dt.timedelta(days=i) for i in range((today - first_of_month).days)]

    if cached is not None and len(cached):
        last = pd.Timestamp(cached["open_time"].max(), unit="ms", tz="UTC").date()
        months = [m for m in months if m >= f"{last.year:04d}-{last.month:02d}"]
        days = [d for d in days if d >= last]

    urls = [f"{BASE}/data/futures/um/monthly/klines/{symbol}/{tf}/{symbol}-{tf}-{m}.zip" for m in months]
    urls += [f"{BASE}/data/futures/um/daily/klines/{symbol}/{tf}/{symbol}-{tf}-{d.isoformat()}.zip" for d in days]

    with ThreadPoolExecutor(workers) as ex:
        parts = [p for p in ex.map(_fetch_zip, urls) if p is not None]
    frames = ([cached] if cached is not None else []) + parts
    if not frames:
        return pd.DataFrame(columns=KLINE_COLS)
    df = pd.concat(frames, ignore_index=True)
    df = clean_klines(df, tf)
    df.to_parquet(path, index=False)
    return df


def clean_klines(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    df = df.drop(columns=[c for c in ("ignore",) if c in df.columns])
    for c in ("open", "high", "low", "close", "volume", "quote_volume", "taker_buy_volume", "taker_buy_quote_volume"):
        df[c] = df[c].astype("float64")
    df["open_time"] = df["open_time"].astype("int64")
    # some old files store microseconds
    us = df["open_time"] > 10 ** 14
    df.loc[us, "open_time"] //= 1000
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    df = df[df["open_time"] < _now_ms() - TF_MINUTES[tf] * 60_000]  # drop the still-forming bar
    return df.reset_index(drop=True)


def _now_ms() -> int:
    return int(time.time() * 1000)


def quality_report(df: pd.DataFrame, tf: str) -> dict:
    step = TF_MINUTES[tf] * 60_000
    d = np.diff(df["open_time"].to_numpy())
    if tf == "1w":
        step = 7 * 86_400_000
    return {
        "rows": len(df),
        "start": str(pd.Timestamp(df["open_time"].iloc[0], unit="ms")),
        "end": str(pd.Timestamp(df["open_time"].iloc[-1], unit="ms")),
        "gaps": int((d != step).sum()),
        "zero_volume": int((df["volume"] == 0).sum()),
    }


def resample_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Build Monday-00:00-UTC weekly klines from daily klines (Binance/TradingView weeks).

    data.binance.vision's monthly 1w files are incomplete, so weeks are rebuilt
    from 1d. The still-forming current week is dropped."""
    d = daily.copy()
    ts = pd.to_datetime(d["open_time"], unit="ms", utc=True)
    week = (ts - pd.to_timedelta(ts.dt.dayofweek, unit="D")).dt.normalize()
    d["week"] = week
    g = d.groupby("week", sort=True)
    w = pd.DataFrame({
        "open_time": g["week"].first().map(lambda x: int(x.timestamp() * 1000)).astype("int64"),
        "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
        "close": g["close"].last(), "volume": g["volume"].sum(),
        "quote_volume": g["quote_volume"].sum(), "count": g["count"].sum(),
        "taker_buy_volume": g["taker_buy_volume"].sum(),
        "taker_buy_quote_volume": g["taker_buy_quote_volume"].sum(),
        "ndays": g["open"].size(),
    }).reset_index(drop=True)
    # drop an incomplete last week, keep a listing week even if partial
    if len(w) and w["ndays"].iloc[-1] < 7:
        w = w.iloc[:-1]
    w["close_time"] = w["open_time"] + 7 * 86_400_000 - 1
    return w.drop(columns="ndays")


def download_range(symbol: str, tf: str, start: str, end: str, market: str = "futures/um",
                   workers: int = 8) -> pd.DataFrame:
    """Monthly klines for [start month, end month] from one market ("futures/um" or "spot"). No caching."""
    months = _month_range(dt.date.fromisoformat(start), dt.date.fromisoformat(end))
    urls = [f"{BASE}/data/{market}/monthly/klines/{symbol}/{tf}/{symbol}-{tf}-{m}.zip" for m in months]
    with ThreadPoolExecutor(workers) as ex:
        parts = [p for p in ex.map(_fetch_zip, urls) if p is not None]
    if not parts:
        return pd.DataFrame(columns=KLINE_COLS)
    return clean_klines(pd.concat(parts, ignore_index=True), tf)


def backfill(path: Path, extra: pd.DataFrame) -> pd.DataFrame:
    """Merge older klines into an existing parquet file (dedupe on open_time)."""
    path = Path(path)
    cur = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    df = pd.concat([extra, cur], ignore_index=True)
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    df.to_parquet(path, index=False)
    return df


def download_funding(symbol: str, months: list[str], workers: int = 8) -> pd.DataFrame:
    """Realised funding rates (USDT-M perps) for the given 'YYYY-MM' months: calc_time (ms), interval_h, rate.
    Months not yet published (the running month) are simply missing."""
    urls = [f"{BASE}/data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{m}.zip" for m in months]

    def one(url):
        r = _get(url)
        if r is None:
            return None
        c = _get(url + ".CHECKSUM")
        if c is not None and hashlib.sha256(r.content).hexdigest() != c.text.split()[0]:
            raise RuntimeError(f"checksum mismatch {url}")
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            raw = zf.read(zf.namelist()[0])
        head = raw[:64].decode(errors="ignore")
        df = pd.read_csv(io.BytesIO(raw), header=0 if head.startswith("calc_time") else None)
        df.columns = ["calc_time", "interval_h", "rate"]
        return df

    with ThreadPoolExecutor(workers) as ex:
        parts = [p for p in ex.map(one, urls) if p is not None]
    if not parts:
        return pd.DataFrame(columns=["calc_time", "interval_h", "rate"])
    out = pd.concat(parts, ignore_index=True).drop_duplicates("calc_time").sort_values("calc_time")
    return out.astype({"calc_time": "int64", "interval_h": "int64", "rate": "float64"}).reset_index(drop=True)
