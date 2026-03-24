import requests
import json
import time
import pandas as pd
import os
import io
from datetime import datetime
from typing import Dict, List, Any

# NSE Endpoints
NSE_HOME = "https://www.nseindia.com"
NSE_AD = "/api/live-analysis-advance"
NSE_INDICES = "/api/equity-stockIndices?index="

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

# CSV Sources for Sector Mapping
MAPPING_SOURCES = {
    "NIFTY_500": "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "NIFTY_NEXT_50": "https://www.niftyindices.com/IndexConstituent/ind_niftynext50list.csv",
    "MIDCAP_150": "https://www.niftyindices.com/IndexConstituent/ind_niftymidcap150list.csv",
    "SMALLCAP_250": "https://www.niftyindices.com/IndexConstituent/ind_niftysmallcap250list.csv",
}

class HeatmapGenerator:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.sector_map = {}
        self._init_session()

    def _init_session(self):
        """Warm up context/cookies"""
        try:
            self.session.get(NSE_HOME, timeout=10)
        except:
            pass

    def load_sectors(self):
        """Fetch industry mapping from Nifty 500 CSV using session"""
        print("Loading sector mappings using session...")
        for name, url in MAPPING_SOURCES.items():
            try:
                res = self.session.get(url, timeout=15)
                if res.status_code == 200:
                    df = pd.read_csv(io.StringIO(res.text))
                    if 'Symbol' in df.columns and 'Industry' in df.columns:
                        for _, row in df.iterrows():
                            self.sector_map[row['Symbol']] = row['Industry']
                    elif 'SYMBOL' in df.columns: 
                        industry_col = next((c for c in df.columns if 'INDUSTRY' in c.upper() or 'INDUSTRY' == c), None)
                        if not industry_col and 'Industry' in df.columns: industry_col = 'Industry'
                        if industry_col:
                            for _, row in df.iterrows():
                                self.sector_map[row['SYMBOL']] = row[industry_col]
                else:
                    print(f"Failed to fetch {name}: HTTP {res.status_code}")
            except Exception as e:
                print(f"Failed to load sector CSV {name}: {e}")

    def fetch_market_data(self) -> List[Dict[str, Any]]:
        """Fetch the master list with Market Cap"""
        print("Fetching market data...")
        try:
            res = self.session.get(NSE_HOME + NSE_AD, timeout=15)
            data = res.json()
            raw_stocks = data.get("advance", {}).get("data", [])
            return raw_stocks
        except Exception as e:
            print(f"Error fetching AD data: {e}")
            return []

    def run(self):
        self.load_sectors()
        all_stocks = self.fetch_market_data()
        
        # Prepare segments
        indices = {
            "ALL": [],
            "NIFTY_50": [],
            "NIFTY_500": [],
            "FNO": []
        }

        # Fetch index-specific members
        for idx_key in ["NIFTY 50", "NIFTY 500", "SECURITIES IN F&O"]:
            print(f"Categorizing {idx_key}...")
            try:
                res = self.session.get(NSE_HOME + NSE_INDICES + idx_key.replace("&", "%26"), timeout=15)
                idx_data = res.json()
                symbols = {s['symbol'] for s in idx_data.get('data', [])}
                key_map = {
                    "NIFTY 50": "NIFTY_50",
                    "NIFTY 500": "NIFTY_500",
                    "SECURITIES IN F&O": "FNO"
                }
                k = key_map.get(idx_key)
                
                for s in all_stocks:
                    if s['symbol'] in symbols:
                        indices[k].append({
                            "symbol": s['symbol'],
                            "pChange": s['pchange'],
                            "change": s['change'],
                            "ltp": s['lastPrice'],
                            "prevClose": s['previousClose'],
                            "mCap": s['totalMarketCap'],
                            "sector": self.sector_map.get(s['symbol'], "Others")
                        })
            except Exception as e:
                print(f"Failed category {idx_key}: {e}")

        payload = {
            "ts": int(time.time()),
            "updated": datetime.now().strftime("%d-%b-%Y %H:%M:%S"),
            "indices": indices
        }
        
        if not os.path.exists("data"): os.makedirs("data")
        with open("data/heatmap_data.json", "w") as f:
            json.dump(payload, f, indent=2)
            
        print("Heatmap generation complete.")

if __name__ == "__main__":
    gen = HeatmapGenerator()
    gen.run()
