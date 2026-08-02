# -*- coding: utf-8 -*-
"""
paper_trader.py -- Live PAPER tracker for the user's intraday option rules.
No real orders. Logs simulated 5-lot trades with REAL option premiums and
reports a running scorecard to Telegram. Runs each tick 9:30-11:05 IST.

STRATEGY (user-defined, Aug 2026)
  Universe : symbols in today's 9:16 first-minute spike list (morning bot)
             AND live OI spike (>= OI_THR vs yesterday)  -> "common"
  Direction: CALL only if price > VWAP ; PUT only if price < VWAP
             and must match the spike's price direction
  Chase    : skip if |move vs prev close| > 4.0%
  Entry    : 5 lots, strike = 2 strikes OTM from ATM (from live chain)
  Target   : +12% on premium  -> close (WIN)
  Stop     : -25% on premium   -> close (LOSS)
  Time     : hard close at 11:00 IST (TIME)

State persists in paper_state.json + scorecard.csv (committed by the workflow).
Idempotent: safe to run every 5 min; one entry per symbol per day.
"""
import os, sys, json, csv, time
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd
import yfinance as yf

from morning_signal_bot import (FNO_STOCKS, tg_creds, BATCH,
                                BAR1_HIKE_MIN, PRICE_MIN_CHG, MIN_TURNOVER)
from oi_live import fetch_oi_spurts, INDICES as _OI_IDX
from chain_analytics import _session as _chain_session, fetch_chain

IST = timezone(timedelta(hours=5, minutes=30))

OI_THR       = 1.08     # live OI / prev OI to count as an OI spike this early
CHASE_MAX    = 4.0      # skip if abs(move vs prev close) already > this %
STRIKES_OTM  = 2
LOTS         = 5
TARGET_PCT   = 12.0
STOP_PCT     = -25.0
CLOSE_HH_MM  = (11, 0)  # hard close time IST
STATE_F      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_state.json")
SCORE_F      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scorecard.csv")


def _now():
    return datetime.now(IST)


def tg(msg):
    tok, cid = tg_creds()
    if not (tok and cid):
        return
    requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                  data={"chat_id": cid, "text": msg, "parse_mode": "HTML"},
                  timeout=20)


# ---------------------------------------------------------------------------
def load_state():
    if os.path.exists(STATE_F):
        try:
            return json.load(open(STATE_F, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(s):
    json.dump(s, open(STATE_F, "w", encoding="utf-8"), indent=1, default=str)


def fetch_1m():
    """{sym: DataFrame[bar, close, high, low, volume]} today only, 1-min."""
    out = {}
    for i in range(0, len(FNO_STOCKS), BATCH):
        b = FNO_STOCKS[i:i+BATCH]
        try:
            raw = yf.download(" ".join(x + ".NS" for x in b), period="2d",
                              interval="1m", progress=False, auto_adjust=True,
                              group_by="ticker", threads=True)
        except Exception:
            continue
        if raw is None or raw.empty:
            continue
        today = _now().strftime("%Y-%m-%d")
        for x in b:
            try:
                sub = raw[x + ".NS"] if isinstance(raw.columns, pd.MultiIndex) else raw
                sub = sub.reset_index()
                dtc = "Datetime" if "Datetime" in sub.columns else sub.columns[0]
                sub["dt"] = pd.to_datetime(sub[dtc], utc=True).dt.tz_convert(IST)
                sub = sub[sub["dt"].dt.strftime("%Y-%m-%d") == today]
                sub = sub.dropna(subset=["Close"])
                sub = sub[sub["Close"] > 0]
                if len(sub):
                    sub["bar"] = sub["dt"].dt.strftime("%H:%M")
                    out[x] = sub
            except Exception:
                continue
    return out


def vwap(df):
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    v = df["Volume"].fillna(0)
    cv = v.cumsum()
    return float((tp * v).cumsum().iloc[-1] / cv.iloc[-1]) if cv.iloc[-1] > 0 else float(df["Close"].iloc[-1])


def build_morning_list(m1, prev_close):
    """Replicate morning bot: 9:16 first-min >=3x yesterday 9:16 + move + turnover.
    Needs yesterday too, so pull 2-day 1m separately here (cheap)."""
    ml = {}
    # yesterday's 9:16 from a 5d pull
    try:
        raw = yf.download(" ".join(s + ".NS" for s in FNO_STOCKS[:0] or FNO_STOCKS),
                          period="5d", interval="1m", progress=False,
                          auto_adjust=True, group_by="ticker", threads=True)
    except Exception:
        return ml
    days = None
    for x in FNO_STOCKS:
        try:
            sub = raw[x + ".NS"] if isinstance(raw.columns, pd.MultiIndex) else raw
            sub = sub.reset_index()
            dtc = "Datetime" if "Datetime" in sub.columns else sub.columns[0]
            sub["dt"] = pd.to_datetime(sub[dtc], utc=True).dt.tz_convert(IST)
            sub["day"] = sub["dt"].dt.strftime("%Y-%m-%d")
            sub["bar"] = sub["dt"].dt.strftime("%H:%M")
            ds = sorted(sub["day"].unique())
            if len(ds) < 2:
                continue
            t = sub[(sub.day == ds[-1]) & (sub.bar == "09:16")]
            y = sub[(sub.day == ds[-1 - 1]) & (sub.bar == "09:16")]
            if t.empty or y.empty:
                continue
            tv, yv = float(t["Volume"].iloc[0]), float(y["Volume"].iloc[0])
            px = float(t["Close"].iloc[0]); pc = prev_close.get(x, 0)
            if yv <= 0 or tv <= 0 or pc <= 0:
                continue
            chg = (px / pc - 1) * 100
            if tv / yv >= BAR1_HIKE_MIN and abs(chg) >= PRICE_MIN_CHG and tv * px >= MIN_TURNOVER:
                ml[x] = round(chg, 2)         # sign = spike direction
        except Exception:
            continue
    return ml


def pick_strike(chain, direction):
    rows = chain["rows"]; spot = chain["spot"]
    atm_i = min(range(len(rows)), key=lambda i: abs(rows[i]["strike"] - spot))
    j = atm_i + STRIKES_OTM if direction == "CALL" else atm_i - STRIKES_OTM
    j = max(0, min(j, len(rows) - 1))
    r = rows[j]
    if direction == "CALL":
        prem = r["ce_ltp"] or (r["ce_bid"] + r["ce_ask"]) / 2
    else:
        prem = r["pe_ltp"] or (r["pe_bid"] + r["pe_ask"]) / 2
    return r["strike"], float(prem)


def option_price(sess, sym, strike, direction):
    ch = fetch_chain(sess, sym)
    if not ch:
        return None
    for r in ch["rows"]:
        if abs(r["strike"] - strike) < 1e-6:
            if direction == "CALL":
                return r["ce_ltp"] or (r["ce_bid"] + r["ce_ask"]) / 2
            return r["pe_ltp"] or (r["pe_bid"] + r["pe_ask"]) / 2
    return None


def append_score(row):
    new = not os.path.exists(SCORE_F)
    with open(SCORE_F, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "symbol", "dir", "strike", "entry_bar",
                        "entry_prem", "exit_bar", "exit_prem", "ret_pct",
                        "outcome", "lots"])
        w.writerow(row)


def running_stats():
    if not os.path.exists(SCORE_F):
        return ""
    try:
        df = pd.read_csv(SCORE_F)
    except Exception:
        return ""
    if df.empty:
        return ""
    r = df["ret_pct"].astype(float)
    days = df["date"].nunique()
    return (f"\n<b>RUNNING</b> ({len(df)} trades, {days}d): "
            f"win {(r>0).mean()*100:.0f}%  avg {r.mean():+.1f}%  "
            f"total {r.sum():+.0f}% premium")


# ---------------------------------------------------------------------------
def main():
    now = _now()
    if now.weekday() >= 5:
        print("weekend"); return
    hm = now.hour * 60 + now.minute
    if hm < 9 * 60 + 28 or hm > 11 * 60 + 8:
        print("outside 9:28-11:08 window"); return

    today = now.strftime("%Y-%m-%d")
    st = load_state()
    if st.get("day") != today:                    # fresh day
        st = {"day": today, "morning": None, "open": {}, "closed": []}

    # prev closes from DB-free yfinance daily
    if st.get("morning") is None:
        pc = {}
        for i in range(0, len(FNO_STOCKS), BATCH):
            b = FNO_STOCKS[i:i+BATCH]
            try:
                raw = yf.download(" ".join(x + ".NS" for x in b), period="6d",
                                  interval="1d", progress=False, auto_adjust=True,
                                  group_by="ticker", threads=True)
                for x in b:
                    sub = raw[x + ".NS"] if isinstance(raw.columns, pd.MultiIndex) else raw
                    v = sub["Close"].dropna().values
                    if len(v) >= 2:
                        pc[x] = float(v[-2])
            except Exception:
                continue
        st["prev_close"] = pc
        st["morning"] = build_morning_list(None, pc)
        print(f"morning list ({len(st['morning'])}): {list(st['morning'])}")
        if st["morning"]:
            tg("<b>PAPER TRACKER armed</b> " + today +
               f"\nMorning-list watch: {', '.join(st['morning'])}")

    morning = st.get("morning") or {}
    prev_close = st.get("prev_close") or {}
    if not morning:
        save_state(st); print("no morning-list stocks; nothing to trade"); return

    m1 = fetch_1m()
    oi = fetch_oi_spurts()
    sess = _chain_session()
    force_close = hm >= CLOSE_HH_MM[0] * 60 + CLOSE_HH_MM[1]

    # ---- manage open positions ----
    for sym in list(st["open"].keys()):
        pos = st["open"][sym]
        cur = option_price(sess, sym, pos["strike"], pos["dir"])
        if cur is None or cur <= 0:
            continue
        ret = (cur / pos["entry_prem"] - 1) * 100
        outcome = None
        if ret >= TARGET_PCT:      outcome = "TARGET"
        elif ret <= STOP_PCT:      outcome = "STOP"
        elif force_close:          outcome = "TIME"
        if outcome:
            append_score([today, sym, pos["dir"], pos["strike"], pos["entry_bar"],
                          round(pos["entry_prem"], 2), now.strftime("%H:%M"),
                          round(cur, 2), round(ret, 1), outcome, LOTS])
            st["closed"].append(sym)
            del st["open"][sym]
            emo = "\U0001F7E2" if ret > 0 else "\U0001F534"
            tg(f"{emo} <b>PAPER EXIT {outcome}</b>  {sym} {pos['dir']} {pos['strike']:.0f}\n"
               f"entry {pos['entry_prem']:.1f} -> {cur:.1f}  ({ret:+.1f}% premium)")

    # ---- look for new entries (not after force_close) ----
    if not force_close:
        for sym, spike_chg in morning.items():
            if sym in st["open"] or sym in st["closed"]:
                continue
            if sym not in m1 or sym not in oi:
                continue
            oir = oi[sym]["latest_oi"] / oi[sym]["prev_oi"] if oi[sym]["prev_oi"] else 0
            if oir < OI_THR:                                   # OI spike gate
                continue
            df = m1[sym]
            price = float(df["Close"].iloc[-1])
            pc = prev_close.get(sym, 0)
            if pc <= 0:
                continue
            chg = (price / pc - 1) * 100
            if abs(chg) > CHASE_MAX:                           # chase filter
                continue
            vw = vwap(df)
            up = spike_chg > 0
            # direction must match spike AND price vs VWAP
            if up and not (price > vw):
                continue
            if (not up) and not (price < vw):
                continue
            direction = "CALL" if up else "PUT"
            ch = fetch_chain(sess, sym)
            if not ch:
                continue
            strike, prem = pick_strike(ch, direction)
            if prem <= 0:
                continue
            st["open"][sym] = {"dir": direction, "strike": strike,
                               "entry_prem": prem, "entry_bar": now.strftime("%H:%M"),
                               "spike_chg": spike_chg}
            tg(f"\U0001F4CB <b>PAPER ENTRY</b>  {sym} {direction} {strike:.0f} "
               f"({STRIKES_OTM} OTM)\nprem {prem:.1f} x {LOTS} lots  "
               f"| spot {price:.1f} vs VWAP {vw:.1f}  | OI {oir:.2f}x  "
               f"| move {chg:+.1f}%\ntarget +{TARGET_PCT:.0f}% / stop {STOP_PCT:.0f}% / close 11:00")

    # ---- daily wrap at/after 11:00 ----
    if force_close and not st.get("wrapped"):
        st["wrapped"] = True
        todays = [r for r in _read_today(today)]
        if todays:
            wins = sum(1 for r in todays if float(r["ret_pct"]) > 0)
            body = "\n".join(
                f"{'🟢' if float(r['ret_pct'])>0 else '🔴'} {r['symbol']} {r['dir']} "
                f"{r['ret_pct']}% [{r['outcome']}]" for r in todays)
            tg(f"<b>PAPER SCORECARD</b> {today}\n"
               f"{len(todays)} trades | {wins} win ({wins/len(todays)*100:.0f}%)\n"
               f"{body}{running_stats()}")
        else:
            tg(f"<b>PAPER SCORECARD</b> {today}\nNo qualifying trades today "
               f"(morning-list stocks never met OI+VWAP+chase rules).")

    save_state(st)
    print(f"open={list(st['open'])} closed={st['closed']}")


def _read_today(today):
    if not os.path.exists(SCORE_F):
        return []
    out = []
    for r in csv.DictReader(open(SCORE_F, encoding="utf-8")):
        if r["date"] == today:
            out.append(r)
    return out


if __name__ == "__main__":
    main()
