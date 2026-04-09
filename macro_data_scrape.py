"""
NSDL Macro Data Scraper (AUC by Country, Debt Utilisation, P-Notes).
Matches the complete structure of the MrChartist FII/DII App.
"""
import json
import re
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests
except ImportError:
    import requests

# Configuration
DATA_DIR     = Path("data")
NSDL_BASE = "https://www.fpi.nsdl.co.in/web"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://www.fpi.nsdl.co.in/"
}

def parse_num(s):
    if not s: return 0.0
    try:
        s = s.replace('&nbsp;', '').replace('\xa0', '').replace(',', '').strip()
        if s.startswith('(') and s.endswith(')'): s = '-' + s[1:-1]
        return float(s)
    except: return 0.0

def fetch_table_data(url, output_filename, row_filter=None):
    print(f"  Fetching: {url}")
    try:
        if 'curl_cffi' in globals().get('requests').__name__:
             r = requests.get(url, impersonate="chrome110", timeout=30)
        else:
             r = requests.get(url, headers=HEADERS, timeout=30)
             
        if r.status_code != 200: return None
        
        soup = BeautifulSoup(r.text, 'html.parser')
        rows = soup.find_all('tr')
        data_list = []
        
        for row in rows:
            cells = [td.get_text(separator=" ").strip() for td in row.find_all(['td', 'th'])]
            if not cells: continue
            if row_filter and not row_filter(cells): continue
            data_list.append(cells)
            
        print(f"    Found {len(data_list)} valid rows.")
        if not data_list: return None

        result = {
            "fetched_at": datetime.now().isoformat(),
            "data": data_list
        }
        (DATA_DIR / output_filename).write_text(json.dumps(result, indent=2))
        print(f"  ✅ Saved {output_filename}")
        return result
    except Exception as e:
        print(f"  ❌ Error fetching {url}: {e}")
    return None

def main():
    DATA_DIR.mkdir(exist_ok=True)
    print("=== NSDL Macro Data Scraper ===")
    
    # 1. Country-wise AUC
    fetch_table_data(
        f"{NSDL_BASE}/Reports/Countrywise_AUC.aspx", 
        "country_auc.json",
        row_filter=lambda c: len(c) > 3 and c[0].isdigit()
    )
    
    # 2. Debt Utilisation
    fetch_table_data(
        f"{NSDL_BASE}/Reports/Debt_Utilisation.aspx", 
        "debt_utilisation.json",
        row_filter=lambda c: len(c) > 2
    )
    
    # 3. ODI / P-Notes
    fetch_table_data(
        f"{NSDL_BASE}/Reports/ODI_PN.aspx", 
        "odi_pn.json",
        row_filter=lambda c: len(c) > 2
    )
    
    # 4. FPI Daily (Trends)
    # This matches the latest daily flows reported by NSDL (different from NSE)
    fetch_table_data(
        f"{NSDL_BASE}/Reports/Latest.aspx",
        "fpi_daily.json",
        row_filter=lambda c: len(c) > 2
    )

if __name__ == "__main__":
    main()
