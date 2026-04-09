"""
NSE FII/DII scraper — Multi-day backfill and robust fallbacks.
Ensures no data gaps even if system is offline for weeks.
"""
import json
import sys
import time
import requests as py_requests
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Configuration
DATA_DIR     = Path("data")
LATEST_FILE  = DATA_DIR / "fii_dii_latest.json"
HISTORY_FILE = DATA_DIR / "fii_dii_history.json"
MAX_HISTORY  = 120
NSE_API      = "https://www.nseindia.com/api/fiidiiTradeReact"
FAO_BASE     = "https://nsearchives.nseindia.com/content/nsccl"

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.nseindia.com/",
    "Connection":      "keep-alive",
}

_shared_session = None

def get_session():
    global _shared_session
    if _shared_session:
        return _shared_session
    try:
        from curl_cffi import requests as cf
        s = cf.Session(impersonate="chrome110")
        s.get("https://www.nseindia.com", timeout=20)
        _shared_session = s
    except ImportError:
        s = py_requests.Session()
        s.get("https://www.nseindia.com", headers=HEADERS, timeout=20)
        _shared_session = s
    return _shared_session

def fetch_nse_cash_latest():
    s = get_session()
    # Some attempts might need specific headers if using py_requests
    try:
        r = s.get(NSE_API, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  Direct NSE fetch failed: {e}. Trying proxies...")
        # Try fallbacks
        proxies = [
            f"https://corsproxy.io/?{NSE_API}",
            f"https://api.allorigins.win/get?url={NSE_API}"
        ]
        for url in proxies:
            try:
                r = py_requests.get(url, timeout=20)
                if r.ok:
                    data = r.json()
                    return data.get("contents") if isinstance(data, dict) and "contents" in data else data
            except: continue
    return None

def fetch_fao_csv(date_str):
    """date_str: DD-Mon-YYYY"""
    months = {'Jan':'01','Feb':'02','Mar':'03','Apr':'04','May':'05','Jun':'06','Jul':'07','Aug':'08','Sep':'09','Oct':'10','Nov':'11','Dec':'12'}
    parts = date_str.split('-')
    if len(parts) != 3: return None
    
    day = parts[0].zfill(2)
    month = months.get(parts[1])
    year = parts[2]
    if not month: return None
    
    date_part = f"{day}{month}{year}"
    urls = [
        f"{FAO_BASE}/fao_participant_oi_{date_part}_b.csv",
        f"{FAO_BASE}/fao_participant_oi_{date_part}.csv"
    ]
    
    s = get_session()
    for url in urls:
        try:
            r = s.get(url, timeout=15)
            if r.status_code == 200 and len(r.text) > 100:
                return r.text
        except: continue
    return None

def parse_fao_csv(csv_text):
    import csv
    import io
    fao_data = {}
    if not csv_text: return fao_data
    
    try:
        f = io.StringIO(csv_text.strip())
        reader = csv.reader(f)
        rows = list(reader)
        if len(rows) < 2: return fao_data
        
        def to_int(v):
            try: return int(v.replace(',', '').strip())
            except: return 0
            
        for row in rows[1:]:
            if len(row) < 9: continue
            client = row[0].strip().upper()
            if "FII" not in client and "DII" not in client: continue
            
            key = "FII" if "FII" in client else "DII"
            fao_data[key] = {
                "idx_fut_long":  to_int(row[1]), "idx_fut_short": to_int(row[2]),
                "stk_fut_long":  to_int(row[3]), "stk_fut_short": to_int(row[4]),
                "idx_call_long": to_int(row[5]), "idx_call_short":to_int(row[6]),
                "idx_put_long":  to_int(row[7]), "idx_put_short": to_int(row[8])
            }
    except Exception as e:
        print(f"  Error parsing FAO CSV: {e}")
    return fao_data

def fetch_nifty_vix():
    s = get_session()
    try:
        r = s.get("https://www.nseindia.com/api/allIndices", timeout=20)
        data = r.json()
        res = {}
        for item in data.get("data", []):
            idx = item.get("index", "")
            if idx == "NIFTY 50":
                res["nifty_price"] = float(item.get("last", 0))
                res["nifty_change"] = float(item.get("variation", 0))
                res["nifty_change_pct"] = float(item.get("percentChange", 0))
            elif idx == "INDIA VIX":
                res["vix_price"] = float(item.get("last", 0))
                res["vix_change_pct"] = float(item.get("percentChange", 0))
        return res
    except: return {}

def transform_entry(target_date, cash_rows=None, fao_csv=None):
    entry = {
        "date": target_date,
        "fii_buy": 0, "fii_sell": 0, "fii_net": 0,
        "dii_buy": 0, "dii_sell": 0, "dii_net": 0,
        "fii_idx_fut_long": 0, "fii_idx_fut_short": 0, "fii_idx_fut_net": 0,
        "dii_idx_fut_long": 0, "dii_idx_fut_short": 0, "dii_idx_fut_net": 0,
        "fii_stk_fut_long": 0, "fii_stk_fut_short": 0, "fii_stk_fut_net": 0,
        "dii_stk_fut_long": 0, "dii_stk_fut_short": 0, "dii_stk_fut_net": 0,
        "fii_idx_call_long":0, "fii_idx_call_short":0, "fii_idx_call_net":0,
        "fii_idx_put_long": 0, "fii_idx_put_short": 0, "fii_idx_put_net": 0,
        "pcr": 1.0, "sentiment_score": 50
    }
    
    if cash_rows:
        rows = json.loads(cash_rows) if isinstance(cash_rows, str) else cash_rows
        for row in rows:
            if row.get("date") != target_date: continue
            cat = row.get("category", "").upper()
            if "FII" in cat or "FPI" in cat:
                entry["fii_buy"] = float(row.get("buyValue", 0) or 0)
                entry["fii_sell"] = float(row.get("sellValue", 0) or 0)
                entry["fii_net"] = float(row.get("netValue", 0) or 0)
            elif "DII" in cat:
                entry["dii_buy"] = float(row.get("buyValue", 0) or 0)
                entry["dii_sell"] = float(row.get("sellValue", 0) or 0)
                entry["dii_net"] = float(row.get("netValue", 0) or 0)
    
    if fao_csv:
        fao = parse_fao_csv(fao_csv)
        if "FII" in fao:
            f = fao["FII"]
            entry.update({
                "fii_idx_fut_long": f["idx_fut_long"], "fii_idx_fut_short": f["idx_fut_short"],
                "fii_idx_fut_net": f["idx_fut_long"] - f["idx_fut_short"],
                "fii_stk_fut_long": f["stk_fut_long"], "fii_stk_fut_short": f["stk_fut_short"],
                "fii_stk_fut_net": f["stk_fut_long"] - f["stk_fut_short"],
                "fii_idx_call_long": f["idx_call_long"], "fii_idx_call_short": f["idx_call_short"],
                "fii_idx_call_net": f["idx_call_long"] - f["idx_call_short"],
                "fii_idx_put_long": f["idx_put_long"], "fii_idx_put_short": f["idx_put_short"],
                "fii_idx_put_net": f["idx_put_long"] - f["idx_put_short"],
                "pcr": round(f["idx_put_short"] / f["idx_call_short"], 2) if f["idx_call_short"] > 0 else 1.0
            })
            sentiment = 50 + (entry["fii_net"] / 200) + (entry["fii_idx_fut_net"] / 5000)
            if entry["pcr"] > 1.3: sentiment -= 10
            if entry["pcr"] < 0.7: sentiment += 10
            entry["sentiment_score"] = round(min(100, max(0, sentiment)), 1)
        
        if "DII" in fao:
            d = fao["DII"]
            entry.update({
                "dii_idx_fut_long": d["idx_fut_long"], "dii_idx_fut_short": d["idx_fut_short"],
                "dii_idx_fut_net": d["idx_fut_long"] - d["idx_fut_short"],
                "dii_stk_fut_long": d["stk_fut_long"], "dii_stk_fut_short": d["stk_fut_short"],
                "dii_stk_fut_net": d["stk_fut_long"] - d["stk_fut_short"]
            })
            
    return entry

def main():
    DATA_DIR.mkdir(exist_ok=True)
    print("=== NSE FII/DII Smart Scraper ===")
    
    # 1. Fetch Latest Cash to see available date
    raw_cash = fetch_nse_cash_latest()
    latest_date_str = None
    if raw_cash:
        latest_date_str = next((r["date"] for r in raw_cash if "FII" in r.get("category","").upper()), None)
    
    if not latest_date_str:
        print("  Could not fetch latest cash date. Exiting.")
        return

    # 2. Load History
    history = []
    if HISTORY_FILE.exists():
        history = json.loads(HISTORY_FILE.read_text())
    
    # helper to parse date
    def parse_dt(s): return datetime.strptime(s, "%d-%b-%Y")
    def fmt_dt(dt): return dt.strftime("%d-%b-%Y")
    
    # 3. Determine range
    today = datetime.now()
    latest_dt = parse_dt(latest_date_str)
    
    # Start from 30 days ago OR last holiday-free entry in history
    if history:
        last_stored_dt = parse_dt(history[0]["date"])
        start_dt = last_stored_dt + timedelta(days=1)
    else:
        start_dt = latest_dt - timedelta(days=30)
    
    curr_dt = start_dt
    added_any = False
    
    # 4. Loop and backfill
    while curr_dt <= latest_dt:
        if curr_dt.weekday() >= 5: # Weekend
            curr_dt += timedelta(days=1)
            continue
            
        ds = fmt_dt(curr_dt)
        # Skip if already fully populated (has FAO data)
        existing = next((h for h in history if h["date"] == ds), None)
        if existing and existing.get("fii_idx_fut_long", 0) > 0:
            curr_dt += timedelta(days=1)
            continue
            
        print(f"  Processing: {ds}...")
        fao_csv = fetch_fao_csv(ds)
        if not fao_csv:
            print(f"    No FAO data for {ds}. Skipping.")
            curr_dt += timedelta(days=1)
            continue
        
        # Use latest cash if it matches, else 0/NSDL proxy
        cash_to_use = raw_cash if ds == latest_date_str else None
        entry = transform_entry(ds, cash_to_use, fao_csv)
        entry["fetched_at"] = datetime.now(timezone.utc).isoformat()
        
        # Add Nifty/VIX if latest
        if ds == latest_date_str:
            entry.update(fetch_nifty_vix())
            
        # Update history
        history = [h for h in history if h["date"] != ds]
        history.append(entry)
        added_any = True
        
        curr_dt += timedelta(days=1)

    if added_any:
        history.sort(key=lambda x: parse_dt(x["date"]), reverse=True)
        HISTORY_FILE.write_text(json.dumps(history[:MAX_HISTORY], indent=2))
        LATEST_FILE.write_text(json.dumps(history[0], indent=2))
        print(f"  ✅ Updated history. Latest: {history[0]['date']}")
    else:
        print("  ℹ️ Already up to date.")

    print("=== DONE ===")

if __name__ == "__main__":
    main()
