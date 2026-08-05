# -*- coding: utf-8 -*-
"""
sheet_logger.py  --  5-minute VOLUME + OI recorder for all F&O stocks
======================================================================
Writes 4 wide CSVs per trading day into data/ (committed to the repo =
permanent archive). A Google Sheet pulls these via Apps Script.

  data/vol_raw_YYYY-MM-DD.csv    cumulative volume at each 5-min snapshot
  data/vol_ratio_YYYY-MM-DD.csv  same, as x-multiple of YESTERDAY'S FULL DAY
  data/oi_raw_YYYY-MM-DD.csv     live futures OI at each snapshot
  data/oi_ratio_YYYY-MM-DD.csv   same, as x-multiple of yesterday's OI

Layout (wide): row = symbol, columns = Symbol, YdayVol, Avg10dVol, OpenVol,
then one column per snapshot time (09:20, 09:25, ...). Each run appends
one new time column, so the row IS the day's build-up.
"""
import os, sys, glob
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from morning_signal_bot import FNO_STOCKS, BATCH
from oi_live import fetch_oi_spurts

IST     = timezone(timedelta(hours=5, minutes=30))
DATA_D  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_D, exist_ok=True)

_INDICES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}


def live_fno_list():
    """Current F&O stocks from the weekly NSE refresh; falls back to static."""
    import csv as _csv
    p = os.path.join(DATA_D, "fno_list.csv")
    if os.path.exists(p):
        try:
            syms = [r["Symbol"] for r in _csv.DictReader(open(p, encoding="utf-8"))
                    if r["Symbol"] not in _INDICES]
            if len(syms) > 50:
                return syms
        except Exception:
            pass
    return FNO_STOCKS


SYMBOLS = None      # resolved in main()


def _now():
    return datetime.now(IST)


def fetch_volumes(symbols):
    """{sym: (cum_today, yday_full, avg10)}"""
    out = {}
    today = _now().strftime("%Y-%m-%d")
    for i in range(0, len(symbols), BATCH):
        b = symbols[i:i+BATCH]
        tk = " ".join(x + ".NS" for x in b)
        daily = {}
        try:
            dl = yf.download(tk, period="15d", interval="1d", progress=False,
                             auto_adjust=True, group_by="ticker", threads=True)
            for x in b:
                try:
                    s = dl[x + ".NS"] if isinstance(dl.columns, pd.MultiIndex) else dl
                    s = s.dropna(subset=["Close"])
                    if len(s) >= 2:
                        v = s["Volume"].astype(float)
                        daily[x] = (float(v.iloc[-2]), float(v.iloc[-11:-1].mean()))
                except Exception:
                    continue
        except Exception:
            continue
        try:
            m1 = yf.download(tk, period="1d", interval="1m", progress=False,
                             auto_adjust=True, group_by="ticker", threads=True)
        except Exception:
            continue
        for x in b:
            if x not in daily:
                continue
            try:
                s = m1[x + ".NS"] if isinstance(m1.columns, pd.MultiIndex) else m1
                s = s.reset_index()
                dtc = "Datetime" if "Datetime" in s.columns else s.columns[0]
                s["dt"] = pd.to_datetime(s[dtc], utc=True).dt.tz_convert(IST)
                s = s[s["dt"].dt.strftime("%Y-%m-%d") == today].dropna(subset=["Close"])
                if s.empty:
                    continue
                out[x] = (float(s["Volume"].fillna(0).sum()), *daily[x])
            except Exception:
                continue
    return out


def update_csv(path, col, values, base_cols):
    """Append/overwrite one snapshot column in a wide CSV."""
    if os.path.exists(path):
        df = pd.read_csv(path)
    else:
        df = pd.DataFrame({"Symbol": sorted(base_cols.keys())})
        for k in ("YdayVol", "Avg10dVol", "OpenVol"):
            df[k] = df["Symbol"].map(lambda s: base_cols.get(s, {}).get(k, ""))
    df[col] = df["Symbol"].map(values)
    # keep OpenVol = first snapshot of the day
    if "OpenVol" in df.columns and df["OpenVol"].replace("", pd.NA).isna().all():
        df["OpenVol"] = df["Symbol"].map(values)
    df.to_csv(path, index=False)
    return len(df)


def main():
    now = _now()
    if now.weekday() >= 5:
        print("weekend"); return
    hm = now.hour * 60 + now.minute
    if hm < 9 * 60 + 15 or hm > 15 * 60 + 35:
        print("outside market hours"); return

    day = now.strftime("%Y-%m-%d")
    col = now.strftime("%H:%M")

    symbols = live_fno_list()
    print(f'universe: {len(symbols)} F&O stocks')
    vol = fetch_volumes(symbols)
    oi  = fetch_oi_spurts()
    if not vol:
        print("no volume data"); return

    base = {s: {"YdayVol": int(v[1]), "Avg10dVol": int(v[2]),
                "OpenVol": ""} for s, v in vol.items()}

    n1 = update_csv(os.path.join(DATA_D, f"vol_raw_{day}.csv"), col,
                    {s: int(v[0]) for s, v in vol.items()}, base)
    n2 = update_csv(os.path.join(DATA_D, f"vol_ratio_{day}.csv"), col,
                    {s: round(v[0] / v[1], 2) if v[1] > 0 else ""
                     for s, v in vol.items()}, base)

    n3 = n4 = 0
    if oi:
        obase = {s: {"YdayVol": int(d.get("prev_oi", 0)), "Avg10dVol": "",
                     "OpenVol": ""} for s, d in oi.items()}
        n3 = update_csv(os.path.join(DATA_D, f"oi_raw_{day}.csv"), col,
                        {s: int(d["latest_oi"]) for s, d in oi.items()}, obase)
        n4 = update_csv(os.path.join(DATA_D, f"oi_ratio_{day}.csv"), col,
                        {s: round(d["latest_oi"] / d["prev_oi"], 3)
                         for s, d in oi.items() if d.get("prev_oi")}, obase)

    print(f"{col}  vol_raw {n1} | vol_ratio {n2} | oi_raw {n3} | oi_ratio {n4} rows")


if __name__ == "__main__":
    main()
