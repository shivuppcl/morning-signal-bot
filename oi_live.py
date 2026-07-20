# -*- coding: utf-8 -*-
"""
oi_live.py  --  Live futures OI for all F&O underlyings (one NSE call)
=======================================================================
Source: NSE oi-spurts API via curl_cffi Chrome impersonation.
Returns {} gracefully when NSE is unreachable (e.g. NSE geo-blocks
non-Indian IPs — possible on cloud runners) so callers can skip the
OI section instead of crashing.

latest_oi / prev_oi share the same futures-contract basis, so their
ratio is a clean "OI spiked vs yesterday" measure.
"""

import time


def fetch_oi_spurts() -> dict:
    """
    {symbol: {"latest_oi": int, "prev_oi": int, "chg_oi": int,
              "chg_oi_pct": float, "spot": float}}
    Empty dict on any failure.
    """
    try:
        from curl_cffi import requests as cffi
    except ImportError:
        return {}
    try:
        s = cffi.Session(impersonate="chrome")
        s.get("https://www.nseindia.com/market-data/oi-spurts", timeout=15)
        time.sleep(0.8)
        r = s.get(
            "https://www.nseindia.com/api/live-analysis-oi-spurts-underlyings",
            headers={
                "Referer": "https://www.nseindia.com/market-data/oi-spurts",
                "Accept": "application/json, text/plain, */*",
            },
            timeout=15,
        )
        if r.status_code != 200 or len(r.content) < 100:
            return {}
        out = {}
        for row in r.json().get("data", []):
            sym    = row.get("symbol", "")
            latest = int(row.get("latestOI", 0) or 0)
            prev   = int(row.get("prevOI",   0) or 0)
            if not sym or latest <= 0 or prev <= 0:
                continue
            out[sym] = {
                "latest_oi":  latest,
                "prev_oi":    prev,
                "chg_oi":     int(row.get("changeInOI", 0) or 0),
                "chg_oi_pct": float(row.get("avgInOI", 0) or 0),
                "spot":       float(row.get("underlyingValue", 0) or 0),
            }
        return out
    except Exception:
        return {}


INDICES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}


def oi_top_section(vol_hike_syms: set | None = None, top_n: int = 15) -> str:
    """
    Formatted Telegram section: top N stocks by OI ratio vs yesterday.
    vol_hike_syms: symbols that also have a volume spike -> marked with fire
    (volume + OI together = strongest institutional footprint).
    Returns "" when OI data is unavailable.
    """
    data = fetch_oi_spurts()
    if not data:
        return ""
    rows = [(s, d) for s, d in data.items() if s not in INDICES]
    rows.sort(key=lambda x: -(x[1]["latest_oi"] / x[1]["prev_oi"]))
    rows = rows[:top_n]
    if not rows:
        return ""
    fire = "\U0001F525"
    vol_hike_syms = vol_hike_syms or set()
    L = ["", f"<b>TOP {len(rows)} OI SPIKES</b> (futures OI vs yesterday)"]
    for s, d in rows:
        ratio = d["latest_oi"] / d["prev_oi"]
        both  = f" {fire}" if s in vol_hike_syms else ""
        L.append(f"<b>{s}</b>  {ratio:.2f}x  ({d['chg_oi']:+,} contracts)"
                 f"  Rs{d['spot']:.1f}{both}")
    L.append(f"{fire} = volume spike AND OI spike together")
    return "\n".join(L)
