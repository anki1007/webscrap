
import requests
import json
import time
import os
from datetime import datetime
import pandas as pd
import io

# ── CONFIG ─────────────────────────────────────────────────────────────
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
    "Referer": "https://www.nseindia.com/",
}

INDICES = {
    "NIFTY_50": "NIFTY 50",
    "NIFTY_NEXT_50": "NIFTY NEXT 50",
    "NIFTY_200": "NIFTY 200",
    "NIFTY_500": "NIFTY 500",
    "NIFTY_MIDCAP_150": "NIFTY MIDCAP 150",
    "NIFTY_SMALLCAP_250": "NIFTY SMALLCAP 250",
    "NIFTY_MIDSMALLCAP_400": "NIFTY MIDSMALLCAP 400",
    "NIFTY_MICROCAP_250": "NIFTY MICROCAP 250",
    "FNO": "SECURITIES IN F&O"
}

# CSV sources for validation/symbols list
SYMBOLS_CSVS = {
    "NIFTY_50": "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
    "NIFTY_NEXT_50": "https://www.niftyindices.com/IndexConstituent/ind_niftynext50list.csv",
    "NIFTY_200": "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv",
    "NIFTY_500": "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "NIFTY_MIDCAP_150": "https://www.niftyindices.com/IndexConstituent/ind_niftymidcap150list.csv",
    "NIFTY_SMALLCAP_250": "https://nsearchives.nseindia.com/content/indices/ind_niftysmallcap250list.csv",
    "NIFTY_MIDSMALLCAP_400": "https://www.niftyindices.com/IndexConstituent/ind_niftymidsmallcap400list.csv",
    "NIFTY_MICROCAP_250": "https://www.niftyindices.com/IndexConstituent/ind_niftymicrocap250_list.csv",
}

def fetch_nse_json(session, path):
    url = f"https://www.nseindia.com{path}"
    resp = session.get(url, headers=NSE_HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()

def main():
    print(f"[{datetime.now()}] Starting Heatmap Data Generation...")
    
    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    # Prime cookies
    session.get("https://www.nseindia.com", timeout=15)
    time.sleep(1)

    all_data = {
        "ts": time.time(),
        "updated": datetime.now().strftime("%d-%b %H:%M"),
        "indices": {}
    }

    for key, nse_name in INDICES.items():
        try:
            print(f"Fetching {nse_name}...")
            # URL encode the index name
            encoded_name = nse_name.replace(" ", "%20").replace("&", "%26")
            path = f"/api/equity-stockIndices?index={encoded_name}"
            
            raw = fetch_nse_json(session, path)
            stocks = []
            for item in raw.get('data', []):
                if item.get('priority') == 1: continue # skip the index summary itself
                
                symbol = item.get('symbol', '')
                if not symbol: continue
                
                stocks.append({
                    "symbol": symbol,
                    "ltp": float(item.get('lastPrice', 0) or 0),
                    "change": float(item.get('change', 0) or 0),
                    "pChange": float(item.get('pChange', 0) or 0),
                    "prevClose": float(item.get('previousClose', 0) or 0)
                })
                
            all_data["indices"][key] = stocks
            print(f"  Got {len(stocks)} symbols for {key}")
            time.sleep(1) # be polite to NSE
            
        except Exception as e:
            print(f"  Error fetching {nse_name}: {e}")

    # Output to file
    out_dir = "data"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "heatmap_data.json")
    
    with open(out_path, "w") as f:
        json.dump(all_data, f, indent=2)
    
    print(f"Successfully generated {out_path} at {datetime.now()}")

if __name__ == "__main__":
    main()
