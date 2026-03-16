"""
NSE FII/DII data scraper — runs in GitHub Actions daily.
Saves data/fii_dii_latest.json and data/fii_dii_history.json
"""
import json
import time
import os
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

DATA_DIR    = Path("data")
LATEST_FILE = DATA_DIR / "fii_dii_latest.json"
HISTORY_FILE= DATA_DIR / "fii_dii_history.json"
MAX_HISTORY = 60   # keep last 60 trading days


def fetch_nse():
    session = requests.Session()
    # Warm up: visit homepage to get cookies
    session.get(NSE_BASE, headers=HEADERS, timeout=15)
    time.sleep(1)
    resp = session.get(NSE_API, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def parse_row(rows):
    """Return dict with fii/dii buy/sell/net + date from NSE response array."""
    result = {"date": None, "fii_buy": 0, "fii_sell": 0, "fii_net": 0,
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

    # Deduplicate: replace existing entry for same date
    history = [h for h in history if h.get("date") != new_entry["date"]]
    history.insert(0, new_entry)
    history = history[:MAX_HISTORY]
    return history


def main():
    DATA_DIR.mkdir(exist_ok=True)

    print("Fetching NSE FII/DII data...")
    rows = fetch_nse()
    print(f"  Got {len(rows)} rows")

    entry = parse_row(rows)
    entry["fetched_at"] = datetime.now(timezone.utc).isoformat()
    # Also keep the raw NSE array for frontend compatibility
    entry["raw"] = rows

    LATEST_FILE.write_text(json.dumps(entry, indent=2))
    print(f"  Saved latest: {entry['date']}")

    history = update_history({k: v for k, v in entry.items() if k != "raw"})
    HISTORY_FILE.write_text(json.dumps(history, indent=2))
    print(f"  History: {len(history)} entries")


if __name__ == "__main__":
    main()
