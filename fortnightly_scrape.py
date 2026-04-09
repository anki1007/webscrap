"""
NSDL Fortnightly Sector-wise FPI Scraper.
Uses curl_cffi and robust URL parsing.
"""
import json
import re
import os
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests
except ImportError:
    import requests

# Configuration
DATA_DIR     = Path("data")
SECTOR_LATEST  = DATA_DIR / "sector_latest.json"
SECTOR_HISTORY = DATA_DIR / "sector_history.json"
NSDL_BASE = "https://www.fpi.nsdl.co.in/web"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
}

KNOWN_SECTORS = [
    'Automobile and Auto Components', 'Capital Goods', 'Chemicals', 'Construction',
    'Construction Materials', 'Consumer Durables', 'Consumer Services', 'Diversified',
    'Fast Moving Consumer Goods', 'Financial Services', 'Forest Materials', 'Healthcare',
    'Information Technology', 'Media, Entertainment & Publication', 'Metals & Mining',
    'Oil, Gas & Consumable Fuels', 'Power', 'Realty', 'Services', 'Telecommunication',
    'Textiles', 'Utilities', 'Sovereign', 'Others'
]

def parse_num(s):
    if not s: return 0.0
    try:
        s = s.replace('&nbsp;', '').replace('\xa0', '').replace(',', '').strip()
        if s.startswith('(') and s.endswith(')'): s = '-' + s[1:-1]
        return float(s)
    except: return 0.0

def get_latest_fortnight_code():
    url = f"{NSDL_BASE}/Reports/FPI_Fortnightly_Selection.aspx"
    print(f"  Checking selection page: {url}")
    try:
        if 'curl_cffi' in globals().get('requests').__name__:
             r = requests.get(url, impersonate="chrome110", timeout=30)
        else:
             r = requests.get(url, headers=HEADERS, timeout=30)
             
        soup = BeautifulSoup(r.text, 'html.parser')
        options = soup.find_all('option')
        for opt in options:
            val = opt.get('value')
            if val and "FIIInvestSector_" in val:
                # Extract e.g. "Mar152026" from "~/.../FIIInvestSector_Mar152026.html"
                match = re.search(r"FIIInvestSector_([A-Za-z0-9]+)\.html", val)
                if match:
                    return match.group(1)
    except Exception as e:
        print(f"  Error getting fortnight code: {e}")
    return None

def fetch_fortnight_report(code):
    url = f"{NSDL_BASE}/StaticReports/Fortnightly_Sector_wise_FII_Investment_Data/FIIInvestSector_{code}.html"
    print(f"  Fetching report: {url}")
    try:
        if 'curl_cffi' in globals().get('requests').__name__:
             r = requests.get(url, impersonate="chrome110", timeout=30)
        else:
             r = requests.get(url, headers=HEADERS, timeout=30)
             
        if r.status_code != 200: 
            print(f"    Failed with status {r.status_code}")
            return None
        
        soup = BeautifulSoup(r.text, 'html.parser')
        text = soup.get_text(separator=" ")
        period_match = re.search(r"Fortnight[^\n]*?(\d{1,2}\s*[A-Za-z]{3}\s*\d{4})[^\n]*?(\d{1,2}\s*[A-Za-z]{3}\s*\d{4})", text, re.I)
        period = period_match.group(0) if period_match else code
        
        sector_data = []
        rows = soup.find_all('tr')
        for row in rows:
            tds = row.find_all(['td', 'th'])
            cells = [td.get_text(separator=" ").strip() for td in tds]
            if len(cells) < 30: continue
            
            sector_name = cells[1]
            matched = next((s for s in KNOWN_SECTORS if s.lower()[:12] in sector_name.lower()), None)
            if not matched or "Total" in sector_name: continue
            
            sector_data.append({
                "sector": matched,
                "equity_auc_inr": parse_num(cells[2]),
                "equity_net_inr": parse_num(cells[27]), 
                "total_auc_inr":  parse_num(cells[13]),
                "total_net_inr":  parse_num(cells[37])
            })
            
        return {
            "date_code": code,
            "period": period.strip(),
            "fetched_at": datetime.now().isoformat(),
            "sectors": sector_data
        }
    except Exception as e:
        print(f"  Error fetching report: {e}")
    return None

def main():
    DATA_DIR.mkdir(exist_ok=True)
    print("=== NSDL Fortnightly Scraper ===")
    
    url = f"{NSDL_BASE}/Reports/FPI_Fortnightly_Selection.aspx"
    print(f"  Fetching all available codes from: {url}")
    codes = []
    try:
        if 'curl_cffi' in globals().get('requests').__name__:
             r = requests.get(url, impersonate="chrome110", timeout=30)
        else:
             r = requests.get(url, headers=HEADERS, timeout=30)
        soup = BeautifulSoup(r.text, 'html.parser')
        options = soup.find_all('option')
        for opt in options:
            val = opt.get('value')
            if val and "FIIInvestSector_" in val:
                match = re.search(r"FIIInvestSector_([A-Za-z0-9]+)\.html", val)
                if match: codes.append(match.group(1))
    except Exception as e:
        print(f"  Error fetching codes: {e}")

    if not codes:
        print("  No codes found. Fallback to Mar312026.")
        codes = ["Mar312026"]

    history = []
    if SECTOR_HISTORY.exists():
        try: history = json.loads(SECTOR_HISTORY.read_text())
        except: pass

    added_any = False
    for code in codes:
        # Check if already in history
        if any(h.get("date_code") == code for h in history):
            continue
            
        report = fetch_fortnight_report(code)
        if report and report["sectors"]:
            history.append(report)
            added_any = True
            print(f"  ✅ Backfilled data for {report['period']}")
        else:
            print(f"  ❌ Failed for {code}")

    if added_any:
        # Sort history by date_code (approximate)
        history.sort(key=lambda x: x.get("date_code", ""), reverse=True)
        SECTOR_HISTORY.write_text(json.dumps(history[:28], indent=2))
        SECTOR_LATEST.write_text(json.dumps(history[0], indent=2))
        print("  Done updating sector history.")
    else:
        print("  Sector history already up to date.")

if __name__ == "__main__":
    main()
