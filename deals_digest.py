# -*- coding: utf-8 -*-
"""
deals_digest.py -- Evening Telegram digest: block/bulk deals on F&O stocks,
cross-referenced with today's volume hikes ("who caused today's signals").
Runs on GitHub Actions ~18:45 IST. Needs: curl_cffi, yfinance, requests.
"""
import sys, time
from datetime import datetime, timedelta, timezone
import requests
import pandas as pd
import yfinance as yf
from morning_signal_bot import FNO_STOCKS, tg_creds, BATCH

IST = timezone(timedelta(hours=5, minutes=30))


def fetch_deals():
    from curl_cffi import requests as cffi
    s = cffi.Session(impersonate="chrome")
    s.get("https://www.nseindia.com/market-data/oi-spurts", timeout=15)
    time.sleep(0.8)
    r = s.get("https://www.nseindia.com/api/snapshot-capital-market-largedeal",
              headers={"Referer": "https://www.nseindia.com/",
                       "Accept": "application/json, */*"}, timeout=15)
    return r.json() if r.status_code == 200 and len(r.content) > 50 else {}


def vol_hikes():
    """{sym: hike} today's volume vs yesterday, daily bars."""
    out = {}
    for i in range(0, len(FNO_STOCKS), BATCH):
        b = FNO_STOCKS[i:i+BATCH]
        try:
            raw = yf.download(" ".join(x + ".NS" for x in b), period="5d",
                              interval="1d", progress=False, auto_adjust=True,
                              group_by="ticker", threads=True)
        except Exception:
            continue
        for x in b:
            try:
                sub = raw[x + ".NS"].dropna(subset=["Volume"])
                v = sub["Volume"].values
                if len(v) >= 2 and v[-2] > 0:
                    out[x] = v[-1] / v[-2]
            except Exception:
                continue
    return out


def fmt(deals, hikes):
    now = datetime.now(IST).strftime("%d-%b %H:%M")
    fire, up = "\U0001F525", "\U0001F4CA"
    fno = set(FNO_STOCKS)
    L = [f"<b>DEALS DIGEST</b>  {now} IST"]

    blocks = deals.get("BLOCK_DEALS_DATA") or []
    if blocks:
        L.append("\n<b>BLOCK DEALS</b> (negotiated, institutional):")
        for d in blocks[:6]:
            sym = (d.get("symbol") or "").upper()
            qty = d.get("qty") or "?"
            L.append(f"{d.get('buySell','?')} <b>{sym or d.get('name','?')[:18]}</b> "
                     f"{qty} sh @ {d.get('watp','?')} — {str(d.get('clientName',''))[:38]}")

    bulks = [d for d in (deals.get("BULK_DEALS_DATA") or [])
             if (d.get("symbol") or "").upper() in fno]
    def _val(d):
        try: return float(str(d.get("qty","0")).replace(",","")) * float(d.get("watp") or 0)
        except Exception: return 0
    bulks.sort(key=_val, reverse=True)
    if bulks:
        L.append(f"\n<b>BULK DEALS on F&O stocks</b> (top by value):")
        for d in bulks[:10]:
            sym = (d.get("symbol") or "").upper()
            h = hikes.get(sym, 0)
            mark = f" {fire}{h:.1f}x vol" if h >= 2.0 else ""
            L.append(f"{d.get('buySell','?')} <b>{sym}</b> "
                     f"{d.get('qty','?')} @ {d.get('watp','?')} — "
                     f"{str(d.get('clientName',''))[:34]}{mark}")
    if len(L) == 1:
        L.append("No deals published yet today.")
    L.append(f"\n{fire} = stock also had >=2x volume hike today (our Rule#1)")
    return "\n".join(L)


def main():
    deals = fetch_deals()
    hikes = vol_hikes()
    msg = fmt(deals, hikes)
    try: print(msg.replace("<b>","").replace("</b>",""))
    except UnicodeEncodeError: print(msg.encode("ascii","ignore").decode())
    if "--dry-run" in sys.argv:
        return
    tok, cid = tg_creds()
    if not (tok and cid):
        sys.exit("no telegram creds")
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      data={"chat_id": cid, "text": msg, "parse_mode": "HTML"},
                      timeout=20)
    print("Telegram:", "SENT" if r.status_code == 200 else f"FAILED {r.text[:120]}")


if __name__ == "__main__":
    main()
