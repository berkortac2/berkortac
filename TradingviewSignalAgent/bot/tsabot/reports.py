"""Text reports (Telegram): current positions and P/L, today's result, trade history, notifications.

All dynamic values are HTML-escaped (messages are sent with parse_mode=HTML)."""
from __future__ import annotations

import datetime as dt
from html import escape

MODE_TR = {"paper": "PAPER (sanal)", "testnet": "TESTNET", "live": "CANLI", "replay": "REPLAY"}
STATE_TR = {"running": "🟢 Çalışıyor", "starting": "🟡 Başlatılıyor", "draining": "🟠 Yeni işlem kapalı (açık pozisyonlar yönetiliyor)",
            "stopped": "⚪ Durdu"}


def num(x: float, d: int = 2) -> str:
    """Turkish number format: 1.234,56"""
    s = f"{abs(x):,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if x < 0 else "") + s


def money(x: float | None) -> str:
    if x is None:
        return "–"
    r = round(float(x), 2)                     # the sign follows the value shown (cents)
    return ("+" if r > 0 else "−" if r < 0 else "") + num(abs(r)) + " $"


def pct(x: float | None) -> str:
    if x is None:
        return "–"
    r = round(float(x), 1)
    return ("+" if r > 0 else "−" if r < 0 else "") + "%" + num(abs(r), 1)


def dot(x: float | None) -> str:
    r = round(float(x or 0), 2)
    return "🟢" if r > 0 else "🔴" if r < 0 else "⚪"


def price(x: float | None) -> str:
    if x is None:
        return "–"
    v = float(x)
    t = f"{v:.1f}" if abs(v) >= 1e5 else f"{v:.6g}"
    if "e" in t:
        t = f"{v:.10f}".rstrip("0").rstrip(".")
    return t


def local(ms: int | None, fmt: str = "%d.%m %H:%M") -> str:
    if not ms:
        return "–"
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).astimezone().strftime(fmt)


def duration(minutes: float) -> str:
    m = int(max(0, minutes))
    return f"{m} dk" if m < 60 else f"{m // 60} sa {m % 60} dk"


def side(d: int) -> str:
    return "LONG" if d > 0 else "SHORT"


def position_line(p: dict, now_ms: int) -> str:
    u = p.get("unrealized")
    mins = (now_ms - int(p.get("entry_time_ms") or now_ms)) / 60000
    return (f"{dot(u)} <b>{escape(p['symbol'])}</b> {side(p['direction'])} · {price(p['qty'])} @ {price(p['entry_price'])}"
            f" → {price(p.get('last_price'))}\n     K/Z <b>{money(u)}</b> ({pct(p.get('roe_pct'))} marjin) · "
            f"{duration(mins)} · stop {price(_stop(p))}")


def _stop(p: dict) -> float | None:
    xs = [x for x in (p.get("sl"), p.get("emergency")) if x is not None]
    if not xs:
        return None
    return max(xs) if p["direction"] > 0 else min(xs)


def report(snap: dict, now_ms: int, today: dict) -> str:
    """/rapor: bot state, budget, today's result, every open position with its P/L."""
    pos = snap.get("positions") or []
    unreal = sum(float(p.get("unrealized") or 0) for p in pos)
    real_today = float(snap.get("realized_today") or 0)
    lines = [f"📊 <b>TSA Bot</b> · {local(now_ms)}",
             f"Durum: {STATE_TR.get(snap.get('state', 'stopped'), escape(str(snap.get('status'))))} · "
             f"{MODE_TR.get(snap.get('mode'), escape(str(snap.get('mode'))))}",
             f"Bütçe: {num(snap.get('budget') or 0)} $ · kullanılan marjin {num(snap.get('margin_used') or 0)} $ "
             f"({len(pos)}/{snap.get('max_positions', '–')} pozisyon)",
             "",
             "<b>Bugün</b>",
             f"{dot(real_today)} Kapanan işlemler: <b>{money(real_today)}</b> ({today.get('trades', 0)} işlem, "
             f"{today.get('wins', 0)} kazançlı)",
             f"{dot(unreal)} Açık pozisyonlar: <b>{money(unreal)}</b>",
             f"{dot(real_today + unreal)} Toplam: <b>{money(real_today + unreal)}</b>",
             f"Bütçe dönemi toplamı: {money(snap.get('realized_period'))}"]
    lines += ["", f"<b>Açık pozisyonlar ({len(pos)})</b>"]
    if pos:
        lines += [position_line(p, now_ms) for p in sorted(pos, key=lambda p: -(p.get("unrealized") or 0))]
    else:
        lines.append("Açık pozisyon yok.")
    if snap.get("external"):
        lines += ["", "Bot dokunmuyor (senin pozisyonların): " + escape(", ".join(snap["external"]))]
    return "\n".join(lines)


def positions(snap: dict, now_ms: int) -> str:
    pos = snap.get("positions") or []
    if not pos:
        return "Açık pozisyon yok."
    unreal = sum(float(p.get("unrealized") or 0) for p in pos)
    return "\n".join([f"<b>Açık pozisyonlar ({len(pos)})</b> · toplam {dot(unreal)} <b>{money(unreal)}</b>"]
                     + [position_line(p, now_ms) for p in sorted(pos, key=lambda p: -(p.get("unrealized") or 0))])


def history(trades: list[dict], summary: dict, title: str) -> str:
    """/gecmis: every closed trade of the period with its net result (commission included)."""
    lines = [f"📒 <b>{escape(title)}</b>"]
    if not trades:
        return lines[0] + "\nBu dönemde kapanan işlem yok."
    tf = "%H:%M" if _same_day(trades) else "%d.%m %H:%M"
    for t in sorted(trades, key=lambda t: t["exit_time"]):
        ret = t["pnl"] / t["margin"] * 100 if t.get("margin") else None
        lines.append(f"{dot(t['pnl'])} {local(t['exit_time'], tf)} "
                     f"<b>{escape(t['symbol'])}</b> {side(t['direction'])} {price(t['entry_price'])} → "
                     f"{price(t['exit_price'])}  <b>{money(t['pnl'])}</b> ({pct(ret)}) · {escape(str(t['reason']))}")
    wr = summary.get("win_rate")
    lines += ["", f"{dot(summary['net_pnl'])} Toplam: <b>{money(summary['net_pnl'])}</b> · {summary['trades']} işlem, "
                  f"{summary['wins']} kazançlı" + (f" (%{num(100 * wr, 0)})" if wr is not None else "")
              + f" · ödenen komisyon {num(summary['fees'])} $ (dahil)"]
    return "\n".join(lines)


def _same_day(trades) -> bool:
    days = {local(t["exit_time"], "%Y%m%d") for t in trades}
    return len(days) == 1


def trade_open(d: dict) -> str:
    return (f"🔔 <b>Pozisyon açıldı</b> · {MODE_TR.get(d.get('mode'), '')}\n"
            f"<b>{escape(d['symbol'])}</b> {side(d['direction'])} {d.get('leverage', 1)}x · {price(d['qty'])} @ {price(d['price'])}\n"
            f"Marjin {num(d['margin'])} $ · borsada stop {price(d.get('stop'))}"
            + (f" · TP {price(d['tp'])}" if d.get("tp") else "") + f" · en geç {d.get('H')} mum")


def trade_close(d: dict) -> str:
    icon = "✅" if d["pnl"] > 0 else "❌"
    return (f"{icon} <b>Pozisyon kapandı</b> · {MODE_TR.get(d.get('mode'), '')}\n"
            f"<b>{escape(d['symbol'])}</b> {side(d['direction'])} {price(d['entry'])} → {price(d['exit'])}\n"
            f"Net <b>{money(d['pnl'])}</b> ({pct(d.get('pct'))} marjin, komisyon dahil) · {duration(d.get('minutes', 0))}\n"
            f"Neden: {escape(str(d.get('reason')))}")


HELP = ("🤖 <b>TSA Bot komutları</b>\n"
        "/rapor – açık pozisyonlar, anlık K/Z ve bugünkü kasa durumu\n"
        "/pozisyonlar – sadece açık pozisyonlar\n"
        "/gecmis – bugünkü işlem geçmişi (/gecmis 7 → son 7 gün)\n"
        "/baslat – botu başlat\n"
        "/durdur – yeni işlem açmayı durdur (açık pozisyonlar kurallarına göre kapanır)\n"
        "/kapat SOLUSDT – bir pozisyonu hemen kapat (onay ister)\n"
        "/hepsinikapat – botun tüm pozisyonlarını kapat (onay ister)\n"
        "/yardim – bu liste\n\n"
        "Türkçe karakterle de yazabilirsin: /başlat, /geçmiş …")
