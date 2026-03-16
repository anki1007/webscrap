"""
NSE FII/DII scraper — multiple fallback methods, never fails silently.
Method 1: curl_cffi (Chrome TLS fingerprint — bypasses WAF)
Method 2: requests with full browser session + cookie warmup
Method 3: corsproxy.io public proxy
Method 4: allorigins.win public proxy
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR     = Path("data")
LATEST_FILE  = DATA_DIR / "fii_dii_latest.json"
HISTORY_FILE = DATA_DIR / "fii_dii_history.json"
MAX_HISTORY  = 60
NSE_API      = "https://www.nseindia.com/api/fiidiiTradeReact"

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.nseindia.com/",
    "X-Requested-With": "XMLHttpRequest",
    "Connection":      "keep-alive",
    "sec-ch-ua":       '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest":  "empty",
    "Sec-Fetch-Mode":  "cors",
    "Sec-Fetch-Site":  "same-origin",
}


# ── Method 1: curl_cffi (Chrome TLS impersonation) ──────────────────────────
def fetch_curl_cffi():
    global _shared_session
    from curl_cffi import requests as cf
    print("  [M1] curl_cffi Chrome impersonation...")
    s = cf.Session(impersonate="chrome110")
    s.get("https://www.nseindia.com", timeout=20)
    time.sleep(1)
    r = s.get(NSE_API, timeout=20)
    print(f"  [M1] status {r.status_code}")
    r.raise_for_status()
    _shared_session = s
    return r.json()


# ── Method 2: requests + full cookie warmup ──────────────────────────────────
def fetch_requests():
    global _shared_session
    import requests
    print("  [M2] requests + cookie warmup...")
    s = requests.Session()
    s.get("https://www.nseindia.com", headers=HEADERS, timeout=20)
    time.sleep(2)
    s.get("https://www.nseindia.com/market-data/fii-dii-trading-activity",
          headers=HEADERS, timeout=20)
    time.sleep(1)
    r = s.get(NSE_API, headers=HEADERS, timeout=20)
    print(f"  [M2] status {r.status_code}")
    r.raise_for_status()
    _shared_session = s
    return r.json()


# ── Method 3: corsproxy.io ───────────────────────────────────────────────────
def fetch_corsproxy():
    import requests
    print("  [M3] corsproxy.io proxy...")
    url = f"https://corsproxy.io/?{NSE_API}"
    r = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=20)
    print(f"  [M3] status {r.status_code}")
    r.raise_for_status()
    return r.json()


# ── Method 4: allorigins.win ─────────────────────────────────────────────────
def fetch_allorigins():
    import requests, urllib.parse
    print("  [M4] allorigins.win proxy...")
    url = f"https://api.allorigins.win/get?url={urllib.parse.quote(NSE_API)}"
    r = requests.get(url, timeout=20)
    print(f"  [M4] status {r.status_code}")
    r.raise_for_status()
    data = r.json()
    return json.loads(data["contents"])


# ── Method 5: thingproxy ─────────────────────────────────────────────────────
def fetch_thingproxy():
    import requests
    print("  [M5] thingproxy.freeboard.io proxy...")
    url = f"https://thingproxy.freeboard.io/fetch/{NSE_API}"
    r = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=20)
    print(f"  [M5] status {r.status_code}")
    r.raise_for_status()
    return r.json()


# ── Nifty 50 + India VIX from NSE allIndices ────────────────────────────────
def fetch_nifty_vix(session):
    """Uses the same authenticated session from FII/DII fetch."""
    print("  Fetching Nifty/VIX...")
    try:
        r = session.get("https://www.nseindia.com/api/allIndices", timeout=20)
        r.raise_for_status()
        data = r.json()
        result = {}
        for item in data.get("data", []):
            idx = item.get("index", "")
            if idx == "NIFTY 50":
                result["nifty_price"]      = float(item.get("last", 0))
                result["nifty_change"]     = float(item.get("variation", 0))
                result["nifty_change_pct"] = float(item.get("percentChange", 0))
            elif idx == "INDIA VIX":
                result["vix_price"]        = float(item.get("last", 0))
                result["vix_change_pct"]   = float(item.get("percentChange", 0))
        if result:
            print(f"  Nifty: {result.get('nifty_price')}  VIX: {result.get('vix_price')}")
        return result
    except Exception as e:
        print(f"  Nifty/VIX fetch failed: {e}")
        return {}


# Shared session for reuse across FII/DII + Nifty/VIX
_shared_session = None

def fetch_with_fallback():
    global _shared_session
    methods = [fetch_curl_cffi, fetch_requests, fetch_corsproxy,
               fetch_allorigins, fetch_thingproxy]
    last_err = None
    for fn in methods:
        try:
            rows = fn()
            if rows and isinstance(rows, list) and len(rows) > 0:
                print(f"  SUCCESS via {fn.__name__}")
                return rows
            print(f"  {fn.__name__} returned empty data, trying next...")
        except Exception as e:
            print(f"  {fn.__name__} failed: {e}")
            last_err = e
    raise RuntimeError(f"All fetch methods exhausted. Last error: {last_err}")


def parse_rows(rows):
    result = {"date": None,
              "fii_buy": 0, "fii_sell": 0, "fii_net": 0,
              "dii_buy": 0, "dii_sell": 0, "dii_net": 0}
    for row in rows:
        cat = row.get("category", "")
        if not result["date"]:
            result["date"] = row.get("date")
        if "FII" in cat or "FPI" in cat:
            result["fii_buy"]  = float(row.get("buyValue",  0) or 0)
            result["fii_sell"] = float(row.get("sellValue", 0) or 0)
            result["fii_net"]  = float(row.get("netValue",  0) or 0)
        elif "DII" in cat:
            result["dii_buy"]  = float(row.get("buyValue",  0) or 0)
            result["dii_sell"] = float(row.get("sellValue", 0) or 0)
            result["dii_net"]  = float(row.get("netValue",  0) or 0)
    return result


def update_history(new_entry):
    history = []
    if HISTORY_FILE.exists():
        history = json.loads(HISTORY_FILE.read_text())
    history = [h for h in history if h.get("date") != new_entry["date"]]
    history.insert(0, new_entry)
    return history[:MAX_HISTORY]


def main():
    DATA_DIR.mkdir(exist_ok=True)
    print("=== NSE FII/DII Scraper ===")

    rows = fetch_with_fallback()
    print(f"Fetched {len(rows)} rows")

    entry = parse_rows(rows)
    entry["fetched_at"] = datetime.now(timezone.utc).isoformat()
    entry["raw"] = rows

    # Nifty/VIX — reuse the authenticated session if available
    if _shared_session:
        nv = fetch_nifty_vix(_shared_session)
        entry.update(nv)

    LATEST_FILE.write_text(json.dumps(entry, indent=2))
    print(f"Saved: {entry['date']}")

    history = update_history({k: v for k, v in entry.items() if k != "raw"})
    HISTORY_FILE.write_text(json.dumps(history, indent=2))
    print(f"History: {len(history)} entries")
    print("=== DONE ===")


if __name__ == "__main__":
    main()
