# -*- coding: utf-8 -*-
"""
rule1_bot.py  --  RULE #1 live crossing alerts -> Telegram
===========================================================
THE ONLY RULE:  today's cumulative volume  >=  2.0x  YESTERDAY'S FULL-DAY volume
                (actually traded, no projection, no averages)

Runs every 3 minutes from 9:19 IST. Alerts a stock the MOMENT it crosses 2x —
once per stock per day (state committed back to the repo). Adds live OI so
vol+OI common stocks are marked with fire.
"""
import os, sys, json
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd
import yfinance as yf

from morning_signal_bot import FNO_STOCKS, tg_creds, BATCH
from oi_live import fetch_oi_spurts

IST        = timezone(timedelta(hours=5, minutes=30))
RULE1_MIN  = 2.0        # cum vol vs yesterday FULL day
OI_THR     = 1.08       # live OI / prev OI to count as OI confirmation
MIN_MOVE   = 0.5        # % move vs prev close to be worth alerting
STATE_F    = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "rule1_state.json")


def _now():
    return datetime.now(IST)


def load_state():
    try:
        s = json.load(open(STATE_F, encoding="utf-8"))
        if s.get("day") == _now().strftime("%Y-%m-%d"):
            return s
    except Exception:
        pass
    return {"day": _now().strftime("%Y-%m-%d"), "alerted": []}


def fetch_today_and_prev():
    """{sym: (cum_vol_today, prev_full_vol, last_price, prev_close)}"""
    out = {}
    for i in range(0, len(FNO_STOCKS), BATCH):
        b = FNO_STOCKS[i:i+BATCH]
        tickers = " ".join(x + ".NS" for x in b)
        # daily -> yesterday's FULL volume + prev close
        prev = {}
        try:
            dl = yf.download(tickers, period="6d", interval="1d", progress=False,
                             auto_adjust=True, group_by="ticker", threads=True)
            for x in b:
                try:
                    sub = dl[x + ".NS"] if isinstance(dl.columns, pd.MultiIndex) else dl
                    sub = sub.dropna(subset=["Close"])
                    if len(sub) >= 2:
                        prev[x] = (float(sub["Volume"].iloc[-2]),
                                   float(sub["Close"].iloc[-2]))
                except Exception:
                    continue
        except Exception:
            continue
        # 1-min -> today's cumulative volume + last price
        try:
            m1 = yf.download(tickers, period="1d", interval="1m", progress=False,
                             auto_adjust=True, group_by="ticker", threads=True)
        except Exception:
            continue
        today = _now().strftime("%Y-%m-%d")
        for x in b:
            if x not in prev:
                continue
            try:
                sub = m1[x + ".NS"] if isinstance(m1.columns, pd.MultiIndex) else m1
                sub = sub.reset_index()
                dtc = "Datetime" if "Datetime" in sub.columns else sub.columns[0]
                sub["dt"] = pd.to_datetime(sub[dtc], utc=True).dt.tz_convert(IST)
                sub = sub[sub["dt"].dt.strftime("%Y-%m-%d") == today]
                sub = sub.dropna(subset=["Close"])
                if sub.empty:
                    continue
                cum = float(sub["Volume"].fillna(0).sum())
                px  = float(sub["Close"].iloc[-1])
                pv, pc = prev[x]
                if cum > 0 and pv > 0 and pc > 0:
                    out[x] = (cum, pv, px, pc)
            except Exception:
                continue
    return out


def main():
    now = _now()
    if now.weekday() >= 5:
        print("weekend"); return
    hm = now.hour * 60 + now.minute
    if hm < 9 * 60 + 16 or hm > 15 * 60 + 20:
        print("outside 9:16-15:20 IST"); return

    st = load_state()
    data = fetch_today_and_prev()
    if not data:
        print("no data"); return
    oi = fetch_oi_spurts()

    crossers = []
    for sym, (cum, pv, px, pc) in data.items():
        hike = cum / pv
        chg  = (px / pc - 1) * 100
        if hike < RULE1_MIN or abs(chg) < MIN_MOVE:
            continue
        if sym in st["alerted"]:
            continue
        oir = (oi[sym]["latest_oi"] / oi[sym]["prev_oi"]
               if sym in oi and oi[sym].get("prev_oi") else 0.0)
        crossers.append({"sym": sym, "hike": hike, "chg": chg, "px": px,
                         "oi": oir, "cum": cum, "pv": pv})

    print(f"{len(data)} scanned | {len(crossers)} new Rule#1 crossers")
    if not crossers:
        return

    crossers.sort(key=lambda r: -r["hike"])
    up, dn, fire = "\U0001F7E2", "\U0001F534", "\U0001F525"
    L = [f"<b>RULE #1 CROSS</b>  {now.strftime('%H:%M')} IST",
         f"cum vol >= {RULE1_MIN:.0f}x YESTERDAY'S FULL DAY (traded)", ""]
    for r in crossers:
        d = up if r["chg"] > 0 else dn
        f = f"  {fire}" if r["oi"] >= OI_THR else ""
        L.append(f"{d} <b>{r['sym']}</b>  {r['hike']:.2f}x  {r['chg']:+.2f}%  "
                 f"Rs{r['px']:.1f}  OI {r['oi']:.2f}x{f}")
        L.append(f"      {int(r['cum']):,} vs {int(r['pv']):,} yday")
    L.append(f"\n{fire} = volume AND OI both spiked (your rule)")
    msg = "\n".join(L)

    tok, cid = tg_creds()
    if tok and cid:
        rr = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                           data={"chat_id": cid, "text": msg, "parse_mode": "HTML"},
                           timeout=20)
        print("Telegram:", "SENT" if rr.status_code == 200 else f"FAIL {rr.text[:100]}")
    st["alerted"].extend(r["sym"] for r in crossers)
    json.dump(st, open(STATE_F, "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    main()
