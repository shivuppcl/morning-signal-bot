# -*- coding: utf-8 -*-
"""
chain_analytics.py  --  Full NSE option chain via v3 API + analytics
=====================================================================
NSE killed the old option-chain APIs for scripts, but the NEW v3 endpoint
works with curl_cffi Chrome impersonation (discovered Jul 2026):

  expiries : /api/option-chain-contract-info?symbol=SYM
  chain    : /api/option-chain-v3?type=Indices|Equity&symbol=SYM&expiry=DD-Mon-YYYY

Analytics per chain: PCR, Max Pain, OI walls, ATM straddle, expected move,
ATM IV, IV skew, top change-in-OI strikes.
"""

import time
from datetime import datetime

_INDICES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}
_expiry_cache: dict = {}          # {symbol: (yyyymmdd_str, expiry)} per day


def _session():
    from curl_cffi import requests as cffi
    s = cffi.Session(impersonate="chrome")
    s.get("https://www.nseindia.com/option-chain", timeout=15)
    time.sleep(0.6)
    return s


_HDRS = {"Referer": "https://www.nseindia.com/option-chain",
         "Accept": "application/json, text/plain, */*"}


def nearest_expiry(sess, symbol: str) -> str | None:
    """Nearest listed expiry (cached per day)."""
    key = datetime.now().strftime("%Y%m%d")
    if _expiry_cache.get(symbol, ("",))[0] == key:
        return _expiry_cache[symbol][1]
    try:
        r = sess.get("https://www.nseindia.com/api/option-chain-contract-info"
                     f"?symbol={symbol}", headers=_HDRS, timeout=15)
        exps = r.json().get("expiryDates", [])
        if not exps:
            return None
        _expiry_cache[symbol] = (key, exps[0])
        return exps[0]
    except Exception:
        return None


def fetch_chain(sess, symbol: str) -> dict:
    """
    Full nearest-expiry chain.
    Returns {"spot", "expiry", "timestamp", "rows":[{strike, ce_oi, ce_chg,
             ce_vol, ce_iv, ce_ltp, pe_ltp, pe_iv, pe_vol, pe_chg, pe_oi}]}
    or {} on failure.
    """
    exp = nearest_expiry(sess, symbol)
    if not exp:
        return {}
    typ = "Indices" if symbol in _INDICES else "Equity"
    try:
        r = sess.get("https://www.nseindia.com/api/option-chain-v3"
                     f"?type={typ}&symbol={symbol}&expiry={exp}",
                     headers=_HDRS, timeout=20)
        recs = r.json().get("records", {})
        data = recs.get("data", [])
        spot = float(recs.get("underlyingValue", 0) or 0)
        if not data or spot <= 0:
            return {}
        rows = []
        for d in data:
            ce, pe = d.get("CE") or {}, d.get("PE") or {}
            rows.append({
                "strike": float(d.get("strikePrice", 0) or 0),
                "ce_oi":  int(ce.get("openInterest", 0) or 0),
                "ce_chg": int(ce.get("changeinOpenInterest", 0) or 0),
                "ce_vol": int(ce.get("totalTradedVolume", 0) or 0),
                "ce_iv":  float(ce.get("impliedVolatility", 0) or 0),
                "ce_ltp": float(ce.get("lastPrice", 0) or 0),
                "pe_ltp": float(pe.get("lastPrice", 0) or 0),
                "pe_iv":  float(pe.get("impliedVolatility", 0) or 0),
                "pe_vol": int(pe.get("totalTradedVolume", 0) or 0),
                "pe_chg": int(pe.get("changeinOpenInterest", 0) or 0),
                "pe_oi":  int(pe.get("openInterest", 0) or 0),
                "ce_bid": float(ce.get("buyPrice1", 0) or 0),
                "ce_ask": float(ce.get("sellPrice1", 0) or 0),
                "pe_bid": float(pe.get("buyPrice1", 0) or 0),
                "pe_ask": float(pe.get("sellPrice1", 0) or 0),
            })
        rows.sort(key=lambda x: x["strike"])
        return {"spot": spot, "expiry": exp,
                "timestamp": recs.get("timestamp", ""), "rows": rows}
    except Exception:
        return {}


def enrich_rows(chain: dict):
    """Add per-strike delta (BS, per-option IV) and spread% to rows."""
    from option_analyzer import bs_greeks
    rows, spot = chain["rows"], chain["spot"]
    try:
        exp_dt = datetime.strptime(chain["expiry"], "%d-%b-%Y")
        T = max((exp_dt - datetime.now()).days, 1) / 365.0
    except Exception:
        T = 30 / 365.0
    for r in rows:
        civ = (r["ce_iv"] or 25) / 100.0
        piv = (r["pe_iv"] or 25) / 100.0
        gc = bs_greeks(spot, r["strike"], T, civ, "CE") or {}
        gp = bs_greeks(spot, r["strike"], T, piv, "PE") or {}
        r["ce_delta"] = round(gc.get("delta", 0), 2)
        r["pe_delta"] = round(gp.get("delta", 0), 2)
        for side in ("ce", "pe"):
            b, a = r[f"{side}_bid"], r[f"{side}_ask"]
            mid = (b + a) / 2
            r[f"{side}_spr"] = round((a - b) / mid * 100, 1) if (b > 0 and a > b) else 99.9


def analyze(chain: dict) -> dict:
    """Analytics from a fetch_chain() result. {} if chain empty."""
    if not chain or not chain.get("rows"):
        return {}
    try:
        enrich_rows(chain)
    except Exception:
        pass
    rows, spot = chain["rows"], chain["spot"]

    tot_ce = sum(r["ce_oi"] for r in rows)
    tot_pe = sum(r["pe_oi"] for r in rows)
    pcr    = round(tot_pe / tot_ce, 3) if tot_ce else 0.0

    # Max pain: strike with minimum total intrinsic payout to option buyers
    strikes = [r["strike"] for r in rows]
    def payout(K):
        return (sum(r["ce_oi"] * max(K - r["strike"], 0) for r in rows) +
                sum(r["pe_oi"] * max(r["strike"] - K, 0) for r in rows))
    max_pain = min(strikes, key=payout) if strikes else 0.0

    call_wall = max(rows, key=lambda r: r["ce_oi"])["strike"]
    put_wall  = max(rows, key=lambda r: r["pe_oi"])["strike"]

    atm = min(rows, key=lambda r: abs(r["strike"] - spot))
    straddle = atm["ce_ltp"] + atm["pe_ltp"]
    exp_move = round(straddle / spot * 100, 2) if spot else 0.0
    atm_iv   = round((atm["ce_iv"] + atm["pe_iv"]) / 2, 1)

    # IV skew: OTM put IV minus OTM call IV, ~3 strikes out
    i_atm = rows.index(atm)
    otm_p = rows[max(i_atm - 3, 0)]
    otm_c = rows[min(i_atm + 3, len(rows) - 1)]
    skew  = round(otm_p["pe_iv"] - otm_c["ce_iv"], 1)

    top_ce_chg = max(rows, key=lambda r: r["ce_chg"])
    top_pe_chg = max(rows, key=lambda r: r["pe_chg"])

    # Kinahan: strike selection by DELTA (0.30 short candidates), not distance
    def _near_delta(side, tgt=0.30):
        cands = [(abs(abs(r.get(f"{side}_delta", 0)) - tgt), r) for r in rows
                 if r.get(f"{side}_oi", 0) > 0]
        return min(cands, key=lambda x: x[0])[1] if cands else None
    sc = _near_delta("ce"); sp = _near_delta("pe")

    return {
        "sell_ce_strike": sc["strike"] if sc else 0,
        "sell_ce_delta":  sc.get("ce_delta", 0) if sc else 0,
        "sell_ce_spr":    sc.get("ce_spr", 0) if sc else 0,
        "sell_pe_strike": sp["strike"] if sp else 0,
        "sell_pe_delta":  sp.get("pe_delta", 0) if sp else 0,
        "sell_pe_spr":    sp.get("pe_spr", 0) if sp else 0,
        "spot": spot, "expiry": chain["expiry"], "pcr": pcr,
        "tot_ce_oi": tot_ce, "tot_pe_oi": tot_pe,
        "max_pain": max_pain, "call_wall": call_wall, "put_wall": put_wall,
        "atm_strike": atm["strike"], "straddle": round(straddle, 2),
        "exp_move_pct": exp_move, "atm_iv": atm_iv, "iv_skew": skew,
        "top_ce_chg_strike": top_ce_chg["strike"], "top_ce_chg": top_ce_chg["ce_chg"],
        "top_pe_chg_strike": top_pe_chg["strike"], "top_pe_chg": top_pe_chg["pe_chg"],
    }


def fetch_all(symbols: list[str]) -> dict:
    """{sym: {"chain":..., "analytics":...}} — one warmed session, serial."""
    out = {}
    try:
        sess = _session()
    except Exception:
        return out
    for sym in symbols:
        ch = fetch_chain(sess, sym)
        if ch:
            out[sym] = {"chain": ch, "analytics": analyze(ch)}
        time.sleep(0.7)
    return out
