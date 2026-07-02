# -*- coding: utf-8 -*-
"""
telegram_command_bot.py  --  /scan on demand from Telegram
===========================================================
Runs every few minutes on GitHub Actions during market hours. Checks the
Telegram bot inbox; if you sent /scan since the last check, runs the full
Rule #1 scan (cumulative volume TODAY vs yesterday's FULL day, actually
traded) and replies with the current top list.

Stateless: Telegram's getUpdates offset acknowledgement means each update
is processed exactly once — no files or database needed between runs.

Commands:
  /scan   full F&O scan -> top volume hikes right now + Rule#1 (>=2x) passers

Env: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID (only this chat is obeyed).
"""

import os, sys, time
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd
import yfinance as yf

from morning_signal_bot import FNO_STOCKS, tg_creds, BATCH
from oi_live import oi_top_section

IST = timezone(timedelta(hours=5, minutes=30))
RULE1_MIN = 2.0
TOP_N     = 15


def api(tok, method, **params):
    r = requests.post(f"https://api.telegram.org/bot{tok}/{method}",
                      data=params, timeout=25)
    return r.json() if r.status_code == 200 else {}


def pending_commands(tok, chat_id):
    """Fetch unread updates; return list of commands from OUR chat only.
    Acknowledge everything so nothing is processed twice."""
    upd = api(tok, "getUpdates", timeout=0).get("result", [])
    if not upd:
        return []
    cmds = []
    for u in upd:
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id", "")) == str(chat_id):
            txt = (msg.get("text") or "").strip().lower()
            if txt.startswith("/scan"):
                cmds.append(txt)
    # acknowledge ALL updates (even non-commands) so they aren't re-read
    api(tok, "getUpdates", offset=upd[-1]["update_id"] + 1, timeout=0)
    return cmds


def fetch_cum_volumes():
    """{sym: (cum_vol_today, yday_full_vol, last_price, yday_close)}"""
    out = {}
    for i in range(0, len(FNO_STOCKS), BATCH):
        batch = FNO_STOCKS[i:i+BATCH]
        try:
            raw = yf.download(" ".join(s + ".NS" for s in batch),
                              period="5d", interval="15m", progress=False,
                              auto_adjust=True, group_by="ticker", threads=True)
        except Exception:
            continue
        if raw is None or raw.empty:
            continue
        for s in batch:
            t = s + ".NS"
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    if t not in raw.columns.get_level_values(0):
                        continue
                    sub = raw[t].copy()
                else:
                    sub = raw.copy()
                sub = sub.reset_index()
                dtc = "Datetime" if "Datetime" in sub.columns else sub.columns[0]
                sub["dt"]   = pd.to_datetime(sub[dtc], utc=True).dt.tz_convert(IST)
                sub["date"] = sub["dt"].dt.strftime("%Y-%m-%d")
                sub = sub.dropna(subset=["Close"])
                sub = sub[sub["Close"] > 0]
                days = sorted(sub["date"].unique())
                if len(days) < 2:
                    continue
                tdf = sub[sub.date == days[-1]]
                ydf = sub[sub.date == days[-2]]
                out[s] = (float(tdf["Volume"].sum()), float(ydf["Volume"].sum()),
                          float(tdf["Close"].iloc[-1]), float(ydf["Close"].iloc[-1]))
            except Exception:
                continue
    return out


def run_scan_message():
    now = datetime.now(IST).strftime("%H:%M")
    today = datetime.now(IST).strftime("%Y-%m-%d")
    data = fetch_cum_volumes()
    rows = []
    for s, (cv, yv, px, yc) in data.items():
        if yv <= 0 or cv <= 0 or yc <= 0:
            continue
        rows.append({"s": s, "hike": cv / yv, "cv": cv, "yv": yv,
                     "px": px, "chg": (px / yc - 1) * 100})
    rows.sort(key=lambda r: -r["hike"])
    top = rows[:TOP_N]
    if not top:
        return f"<b>SCAN</b> {today} {now} IST\nNo data available."

    up, dn, star = "\U0001F7E2", "\U0001F534", "⭐"
    n_pass = sum(1 for r in rows if r["hike"] >= RULE1_MIN)
    L = [f"<b>VOLUME SCAN</b>  {today} {now} IST",
         f"cum vol today vs yesterday FULL day  |  "
         f"{star} = RULE#1 pass (>= {RULE1_MIN:.0f}x traded)  |  {n_pass} passing",
         ""]
    for r in top:
        d = up if r["chg"] > 0 else dn
        mark = f" {star}" if r["hike"] >= RULE1_MIN else ""
        L.append(f"{d} <b>{r['s']}</b>  {r['hike']:.2f}x  "
                 f"{r['chg']:+.2f}%  Rs{r['px']:.1f}{mark}")

    # Top OI spikes vs yesterday (fail-soft: empty when NSE unreachable)
    vol_spikers = {r["s"] for r in rows if r["hike"] >= RULE1_MIN}
    oi_sec = oi_top_section(vol_hike_syms=vol_spikers)
    if oi_sec:
        L.append(oi_sec)
    else:
        L.append("\n(OI data unavailable from this server — NSE may block "
                 "non-Indian IPs)")
    return "\n".join(L)


def main():
    tok, cid = tg_creds()
    if not (tok and cid):
        print("No Telegram credentials."); sys.exit(1)

    cmds = pending_commands(tok, cid)
    print(f"pending /scan commands: {len(cmds)}")
    if not cmds:
        return

    api(tok, "sendMessage", chat_id=cid, parse_mode="HTML",
        text="⏳ Scanning all 206 F&O stocks — results in ~1 minute ...")
    msg = run_scan_message()
    ok = api(tok, "sendMessage", chat_id=cid, text=msg, parse_mode="HTML")
    print("reply sent:", bool(ok.get("ok")))


if __name__ == "__main__":
    main()
