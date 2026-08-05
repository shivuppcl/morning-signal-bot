# -*- coding: utf-8 -*-
"""Weekly F&O list refresh from NSE's official lot-size file."""
import os, csv
from datetime import datetime, timedelta, timezone
IST = timezone(timedelta(hours=5, minutes=30))
DATA_D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_D, exist_ok=True)
OUT = os.path.join(DATA_D, "fno_list.csv")

def fetch():
    from curl_cffi import requests as cffi
    r = cffi.Session(impersonate="chrome").get(
        "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv", timeout=20)
    if r.status_code != 200:
        return {}
    lines = [l for l in r.text.splitlines() if "," in l]
    out = {}
    for ln in lines[1:]:
        p = [c.strip() for c in ln.split(",")]
        if len(p) > 2:
            try:
                lot = int(p[2])
                if lot > 0:
                    out[p[1].upper()] = lot
            except Exception:
                continue
    return out

def main():
    new = fetch()
    if not new:
        print("NSE lot file unavailable"); return
    old = {}
    if os.path.exists(OUT):
        for r in csv.DictReader(open(OUT, encoding="utf-8")):
            old[r["Symbol"]] = int(r["LotSize"])
    added   = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Symbol", "LotSize", "Updated"])
        stamp = datetime.now(IST).strftime("%Y-%m-%d")
        for s in sorted(new):
            w.writerow([s, new[s], stamp])
    print(f"F&O list: {len(new)} symbols | added {added} | removed {removed}")

if __name__ == "__main__":
    main()
