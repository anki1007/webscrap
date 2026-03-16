"""
NSE FII/DII data scraper — runs in GitHub Actions daily.
Saves data/fii_dii_latest.json and data/fii_dii_history.json
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

NSE_BASE = "https://www.nseindia.com"
NSE_API  = f"{NSE_BASE}/api/fiidiiTradeReact"
HEADERS  = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.nseindia.com/",
    "Connection":      "keep-alive",
}

DATA_DIR     = Path("data")
LATEST_FILE  = DATA_DIR / "fii_dii_latest.json"
HISTORY_FILE = DATA_DIR / "fii_dii_history.json"
MAX_HISTORY  = 60


def fetch_nse():
    session = requests.Session()
    # Warm-up: get cookies from homepage
    print("  Warming up session...")
    r = session.get(NSE_BASE, headers=HEADERS, timeout=20)
    print(f"  Homepage status: {r.status_code}")
    time.sleep(2)
    print("  Hitting API...")
    resp = session.get(NSE_API, headers=HEADERS, timeout=20)
    print(f"  API status: {resp.status_code}")
    resp.raise_for_status()
    return resp.json()


def parse_row(rows):
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

    print("Fetching NSE FII/DII data...")
    try:
        rows = fetch_nse()
    except Exception as e:
        print(f"ERROR: NSE fetch failed — {e}", file=sys.stderr)
        # Don't hard-fail if NSE is down; preserve existing data
        if LATEST_FILE.exists():
            print("Keeping existing data file unchanged.")
            sys.exit(0)
        sys.exit(1)

    print(f"  Got {len(rows)} rows")

    entry = parse_row(rows)
    entry["fetched_at"] = datetime.now(timezone.utc).isoformat()
    entry["raw"] = rows   # frontend uses this array

    LATEST_FILE.write_text(json.dumps(entry, indent=2))
    print(f"  Saved latest: {entry['date']}")

    history = update_history({k: v for k, v in entry.items() if k != "raw"})
    HISTORY_FILE.write_text(json.dumps(history, indent=2))
    print(f"  History: {len(history)} entries")


if __name__ == "__main__":
    main()
