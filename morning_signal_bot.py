# -*- coding: utf-8 -*-
"""
morning_signal_bot.py  --  9:15 Opening-Bar Volume Spike -> Telegram
=====================================================================
Self-contained: needs only  pip install yfinance requests
No database, no NSE APIs -- runs anywhere (laptop, GitHub Actions, any cloud).

RULE (early-warning version of Rule #1) — FIRST 1-MINUTE CANDLE:
  Signal = today's 9:15 one-minute candle volume >= 3x yesterday's 9:15 candle
           AND |price change vs yesterday close| >= 0.5%
           AND first-minute turnover >= Rs 1 crore  (filters illiquid noise)
  MEGA   = today's FIRST MINUTE alone >= 10% of yesterday's ENTIRE day volume

Telegram credentials: env vars TELEGRAM_TOKEN / TELEGRAM_CHAT_ID,
falling back to telegram_config.ini next to this file.

Usage:
  python morning_signal_bot.py            # wait till 9:17:30 IST if early, scan, send
  python morning_signal_bot.py --dry-run  # scan + print only, no Telegram
  python morning_signal_bot.py --no-wait  # skip the wait (test any time)
"""

import os, sys, time, argparse, configparser
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd
import yfinance as yf

IST = timezone(timedelta(hours=5, minutes=30))

BAR1_HIKE_MIN  = 3.0    # today's first 1-min candle >= 3x yesterday's
PRICE_MIN_CHG  = 0.5    # |% change vs yesterday close|
MEGA_FRAC      = 0.10   # first MINUTE >= 10% of yesterday FULL day = MEGA
MIN_TURNOVER   = 1e7    # Rs 1 crore traded in the first minute (noise filter)
BATCH          = 25
TOP_N          = 12
INTERVAL       = "1m"   # first ONE-MINUTE candle (09:15:00-09:15:59)

FNO_STOCKS = [
    "360ONE", "ABB", "ABCAPITAL", "ADANIENSOL", "ADANIENT", "ADANIGREEN", "ADANIPORTS", "ALKEM",
    "AMBER", "AMBUJACEM", "ANGELONE", "APLAPOLLO", "APOLLOHOSP", "ASHOKLEY", "ASIANPAINT", "ASTRAL",
    "AUBANK", "AUROPHARMA", "AXISBANK", "BAJAJ-AUTO", "BAJAJFINSV", "BAJAJHLDNG", "BAJFINANCE", "BANDHANBNK",
    "BANKBARODA", "BANKINDIA", "BDL", "BEL", "BHARATFORG", "BHARTIARTL", "BHEL", "BIOCON",
    "BLUESTARCO", "BOSCHLTD", "BPCL", "BRITANNIA", "BSE", "CAMS", "CANBK", "CDSL",
    "CGPOWER", "CHOLAFIN", "CIPLA", "COALINDIA", "COFORGE", "COLPAL", "CONCOR", "CROMPTON",
    "CUMMINSIND", "DABUR", "DALBHARAT", "DELHIVERY", "DIVISLAB", "DIXON", "DLF", "DMART",
    "DRREDDY", "EICHERMOT", "ETERNAL", "EXIDEIND", "FEDERALBNK", "FORTIS", "GAIL", "GLENMARK",
    "GMRAIRPORT", "GODREJCP", "GODREJPROP", "GRASIM", "HAL", "HAVELLS", "HCLTECH", "HDFCAMC",
    "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HINDALCO", "HINDPETRO", "HINDUNILVR", "HINDZINC", "HUDCO",
    "ICICIBANK", "ICICIGI", "ICICIPRULI", "IDEA", "IDFCFIRSTB", "IEX", "INDHOTEL", "INDIANB",
    "INDIGO", "INDUSINDBK", "INDUSTOWER", "INFY", "INOXWIND", "IOC", "IREDA", "IRFC",
    "ITC", "JINDALSTEL", "JIOFIN", "JSWENERGY", "JSWSTEEL", "JUBLFOOD", "KALYANKJIL", "KAYNES",
    "KEI", "KFINTECH", "KOTAKBANK", "KPITTECH", "LAURUSLABS", "LICHSGFIN", "LICI", "LODHA",
    "LT", "LTF", "LTM", "LUPIN", "M&M", "MANAPPURAM", "MANKIND", "MARICO",
    "MARUTI", "MAXHEALTH", "MAZDOCK", "MCX", "MFSL", "MOTHERSON", "MPHASIS", "MUTHOOTFIN",
    "NATIONALUM", "NAUKRI", "NBCC", "NESTLEIND", "NHPC", "NMDC", "NTPC", "NUVAMA",
    "NYKAA", "OBEROIRLTY", "OFSS", "OIL", "ONGC", "PAGEIND", "PATANJALI", "PAYTM",
    "PERSISTENT", "PETRONET", "PFC", "PGEL", "PHOENIXLTD", "PIDILITIND", "PIIND", "PNB",
    "PNBHOUSING", "POLICYBZR", "POLYCAB", "POWERGRID", "POWERINDIA", "PPLPHARMA", "PREMIERENE", "PRESTIGE",
    "RBLBANK", "RECLTD", "RELIANCE", "RVNL", "SAIL", "SAMMAANCAP", "SBICARD", "SBILIFE",
    "SBIN", "SHREECEM", "SHRIRAMFIN", "SIEMENS", "SOLARINDS", "SONACOMS", "SRF", "SUNPHARMA",
    "SUPREMEIND", "SUZLON", "SWIGGY", "SYNGENE", "TATACONSUM", "TATAELXSI", "TATAPOWER", "TATASTEEL",
    "TATATECH", "TCS", "TECHM", "TIINDIA", "TITAN", "TMPV", "TORNTPHARM", "TORNTPOWER",
    "TRENT", "TVSMOTOR", "ULTRACEMCO", "UNIONBANK", "UNITDSPR", "UNOMINDA", "UPL", "VBL",
    "VEDL", "VOLTAS", "WAAREEENER", "WIPRO", "YESBANK", "ZYDUSLIFE",
]


def wait_for_bar_close():
    """Wait till 09:18:30 IST — the 09:16 candle (first real trading
    minute; Yahoo reports 09:15 auction candle as zero) closes at 09:17,
    plus ~90s for Yahoo to publish it."""
    now = datetime.now(IST)
    tgt = now.replace(hour=9, minute=18, second=30, microsecond=0)
    if now < tgt and now.hour >= 8:
        s = (tgt - now).total_seconds()
        print(f"waiting {s:.0f}s for the 9:16 one-minute candle ...", flush=True)
        time.sleep(s)


def fetch_bars():
    """{sym: DataFrame[date, bar, volume, close]} from 5d of 1-min data."""
    out = {}
    for i in range(0, len(FNO_STOCKS), BATCH):
        batch = FNO_STOCKS[i:i+BATCH]
        try:
            raw = yf.download(" ".join(s + ".NS" for s in batch),
                              period="5d", interval=INTERVAL, progress=False,
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
                sub["bar"]  = sub["dt"].dt.strftime("%H:%M")
                sub = sub.dropna(subset=["Close"])
                sub = sub[sub["Close"] > 0]
                if len(sub):
                    out[s] = sub[["date", "bar", "Volume", "Close"]].rename(
                        columns={"Volume": "volume", "Close": "close"})
            except Exception:
                continue
    return out


def scan():
    bars = fetch_bars()
    if not bars:
        return [], "", ""
    dates = sorted({d for df in bars.values() for d in df["date"].unique()})
    if len(dates) < 2:
        return [], "", ""
    today, yday = dates[-1], dates[-2]

    rows = []
    for s, df in bars.items():
        t1 = df[(df.date == today) & (df.bar == "09:16")]
        y1 = df[(df.date == yday)  & (df.bar == "09:16")]
        ydf = df[df.date == yday]
        if t1.empty or y1.empty or ydf.empty:
            continue
        yfull = float(ydf["volume"].sum())
        yc    = float(ydf["close"].iloc[-1])
        tv, yv = float(t1["volume"].iloc[0]), float(y1["volume"].iloc[0])
        px     = float(t1["close"].iloc[0])
        if yv <= 0:
            # High-priced/low-share stocks (BOSCHLTD @ Rs42k) often have a ZERO
            # 9:16 candle yesterday — use yesterday's median nonzero 1-min
            # volume as baseline instead of silently skipping the stock.
            _nz = ydf[ydf["volume"] > 0]["volume"]
            yv = float(_nz.median()) if len(_nz) else 0.0
        if yv <= 0 or tv <= 0 or yc <= 0:
            continue
        hike = tv / yv
        chg  = (px / yc - 1) * 100
        if (hike >= BAR1_HIKE_MIN and abs(chg) >= PRICE_MIN_CHG
                and tv * px >= MIN_TURNOVER):
            rows.append({
                "symbol": s, "hike": hike, "bar1_vol": int(tv),
                "yday_bar1": int(yv), "yday_full": int(yfull),
                "price": px, "chg": chg,
                "mega": yfull > 0 and tv >= MEGA_FRAC * yfull,
            })
    rows.sort(key=lambda r: -r["hike"])
    return rows[:TOP_N], today, yday


def tg_creds():
    tok = os.environ.get("TELEGRAM_TOKEN", "").strip()
    cid = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if tok and cid:
        return tok, cid
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "telegram_config.ini"), encoding="utf-8")
    return (cfg.get("telegram", "token",   fallback="").strip(),
            cfg.get("telegram", "chat_id", fallback="").strip())


def fmt(rows, today):
    now = datetime.now(IST).strftime("%H:%M")
    if not rows:
        return (f"<b>9:16 FIRST-MINUTE SCAN</b>  {today} {now} IST\n"
                f"No first-minute spikes (>= {BAR1_HIKE_MIN:.0f}x + "
                f"Rs1cr turnover) today.")
    up, dn, bolt = "\U0001F7E2", "\U0001F534", "⚡"
    L = [f"<b>9:16 FIRST-MINUTE SPIKES</b>  {today} {now} IST",
         f"1-min candle vol vs yesterday's  (>={BAR1_HIKE_MIN:.0f}x, move >="
         f"{PRICE_MIN_CHG}%, >=Rs1cr traded)",
         ""]
    for r in rows:
        d = up if r["chg"] > 0 else dn
        mega = f"  {bolt}MEGA" if r["mega"] else ""
        L.append(f"{d} <b>{r['symbol']}</b>  {r['hike']:.1f}x  "
                 f"{r['chg']:+.2f}%  Rs{r['price']:.1f}{mega}")
        L.append(f"      1st-min {r['bar1_vol']:,} vs {r['yday_bar1']:,}")
    L.append("")
    L.append(f"{bolt}MEGA = first minute alone >= 10% of yesterday's FULL day volume")
    return "\n".join(L)


def send(msg, tok, cid):
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      data={"chat_id": cid, "text": msg, "parse_mode": "HTML"},
                      timeout=20)
    return r.status_code == 200, r.text[:200]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-wait", action="store_true")
    a = ap.parse_args()

    if not a.no_wait:
        wait_for_bar_close()

    print("scanning ...", flush=True)
    rows, today, yday = scan()
    msg = fmt(rows, today)

    # Top-15 OI spikes vs yesterday (visible note if NSE doesn't respond —
    # never a silent absence)
    try:
        from oi_live import oi_top_section
        oi_sec = oi_top_section(vol_hike_syms={r["symbol"] for r in rows})
        msg += ("\n" + oi_sec) if oi_sec else \
               "\n\n(OI data not available right now)"
    except Exception as e:
        msg += f"\n\n(OI section error: {type(e).__name__})"

    try:
        print(msg.replace("<b>", "").replace("</b>", ""))
    except UnicodeEncodeError:
        print(msg.encode("ascii", "ignore").decode())

    if a.dry_run:
        print("\n[dry-run] not sent.")
        return
    tok, cid = tg_creds()
    if not (tok and cid):
        print("No Telegram credentials found.")
        sys.exit(1)
    ok, resp = send(msg, tok, cid)
    print("Telegram:", "SENT" if ok else f"FAILED {resp}")


if __name__ == "__main__":
    main()
