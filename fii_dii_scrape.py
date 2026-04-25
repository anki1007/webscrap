"""
NSE FII/DII scraper — Resilient backfill, holiday-aware, decoupled cash + FAO.

Core invariants (do not break these):
1. Cash and FAO are fetched INDEPENDENTLY per day. Partial data is better than none.
2. The script never overwrites a populated field with zero / empty.
3. Each run scans the last BACKFILL_DAYS days and fills any missing fields,
   so missing days are recovered even after weeks of downtime.
4. Output filenames are canonical and stable across the project:
       data/fii_dii_history.json
       data/fii_dii_latest.json
5. Indian market holidays are skipped — they are NOT "missing" data.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests as py_requests

# ── Paths & constants ────────────────────────────────────────────
DATA_DIR     = Path("data")
LATEST_FILE  = DATA_DIR / "fii_dii_latest.json"
HISTORY_FILE = DATA_DIR / "fii_dii_history.json"
MAX_HISTORY  = 180          # ~9 months of trading days
BACKFILL_DAYS = 90          # how far back to scan for missing data each run

NSE_API     = "https://www.nseindia.com/api/fiidiiTradeReact"
NSE_ARCHIVE = "https://www.nseindia.com/api/fiidii-trade-archive"
ALLINDICES  = "https://www.nseindia.com/api/allIndices"
FAO_BASE    = "https://nsearchives.nseindia.com/content/nsccl"

REPO_OWNER = "anki1007"
REPO_NAME  = "webscrap"
REPO_HISTORY_URL = (
    f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main/data/fii_dii_history.json"
)

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.nseindia.com/",
    "Connection":      "keep-alive",
}

# ── Indian market holidays (NSE) ─────────────────────────────────
# Static list of NSE trading holidays. Update yearly. When a date is in this
# set the scraper does NOT log it as "missing" and does NOT keep retrying.
NSE_HOLIDAYS = {
    # 2026
    "26-Jan-2026",   # Republic Day
    "16-Feb-2026",   # Mahashivratri
    "03-Mar-2026",   # Holi
    "26-Mar-2026",   # Eid (estimated)
    "31-Mar-2026",   # Eid-ul-Fitr (estimated)
    "03-Apr-2026",   # Good Friday
    "14-Apr-2026",   # Ambedkar Jayanti
    "01-May-2026",   # Maharashtra Day
    "15-Aug-2026",   # Independence Day
    "02-Oct-2026",   # Gandhi Jayanti
    "20-Oct-2026",   # Diwali (estimated)
    "21-Oct-2026",   # Diwali Balipratipada (estimated)
    "25-Dec-2026",   # Christmas
    # 2025 (for older backfill scans)
    "26-Jan-2025", "26-Feb-2025", "14-Mar-2025", "31-Mar-2025",
    "10-Apr-2025", "14-Apr-2025", "18-Apr-2025", "01-May-2025",
    "15-Aug-2025", "27-Aug-2025", "02-Oct-2025", "21-Oct-2025",
    "22-Oct-2025", "05-Nov-2025", "25-Dec-2025",
}

_shared_session = None


def parse_dt(s: str) -> datetime:
    return datetime.strptime(s, "%d-%b-%Y")


def fmt_dt(dt: datetime) -> str:
    return dt.strftime("%d-%b-%Y")


def is_trading_day(dt: datetime) -> bool:
    if dt.weekday() >= 5:
        return False
    if fmt_dt(dt) in NSE_HOLIDAYS:
        return False
    return True


def get_session():
    """Single shared session with NSE cookie warmup."""
    global _shared_session
    if _shared_session is not None:
        return _shared_session
    try:
        from curl_cffi import requests as cf
        s = cf.Session(impersonate="chrome110")
        s.get("https://www.nseindia.com", timeout=20)
        _shared_session = s
        print("  [session] curl_cffi (Chrome impersonation) ready")
    except ImportError:
        s = py_requests.Session()
        s.headers.update(HEADERS)
        s.get("https://www.nseindia.com", timeout=20)
        _shared_session = s
        print("  [session] requests fallback ready")
    return _shared_session


# ── Cash data ────────────────────────────────────────────────────
def fetch_nse_cash_latest():
    """Live cash data for the most recent trading day."""
    s = get_session()
    try:
        r = s.get(NSE_API, timeout=20)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  [cash-latest] direct fetch failed: {e}")

    for proxy in (
        f"https://corsproxy.io/?{NSE_API}",
        f"https://api.allorigins.win/get?url={NSE_API}",
    ):
        try:
            r = py_requests.get(proxy, timeout=20)
            if not r.ok:
                continue
            data = r.json()
            return data.get("contents") if isinstance(data, dict) and "contents" in data else data
        except Exception:
            continue
    return None


def fetch_nse_archive(from_dt: datetime, to_dt: datetime):
    """Historical cash data. NSE deprecated /api/fiidii-trade-archive (returns 404
    as of Apr 2026), so this is now a thin wrapper that returns []. Kept for
    interface stability — historical gaps must be filled from the repo fallback
    or by capturing the data live on the day it is published."""
    return []


# ── F&O participant CSV ──────────────────────────────────────────
def fetch_fao_csv(date_str: str):
    """Fetch participant-wise OI CSV for a given trading day. date_str = DD-Mon-YYYY."""
    months = {
        "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
        "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
        "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
    }
    parts = date_str.split("-")
    if len(parts) != 3:
        return None
    day, mon, year = parts[0].zfill(2), months.get(parts[1]), parts[2]
    if not mon:
        return None
    date_part = f"{day}{mon}{year}"

    urls = [
        f"{FAO_BASE}/fao_participant_oi_{date_part}_b.csv",
        f"{FAO_BASE}/fao_participant_oi_{date_part}.csv",
    ]
    s = get_session()
    for url in urls:
        try:
            r = s.get(url, timeout=15)
            if r.status_code == 200 and len(r.text) > 100:
                return r.text
        except Exception:
            continue
    return None


def parse_fao_csv(csv_text: str):
    out = {}
    if not csv_text:
        return out
    try:
        reader = csv.reader(io.StringIO(csv_text.strip()))
        rows = list(reader)
        if len(rows) < 2:
            return out

        def to_int(v: str) -> int:
            try:
                return int(v.replace(",", "").strip())
            except Exception:
                return 0

        for row in rows[1:]:
            if len(row) < 9:
                continue
            client = row[0].strip().upper()
            if "FII" not in client and "DII" not in client:
                continue
            key = "FII" if "FII" in client else "DII"
            out[key] = {
                "idx_fut_long":   to_int(row[1]),
                "idx_fut_short":  to_int(row[2]),
                "stk_fut_long":   to_int(row[3]),
                "stk_fut_short":  to_int(row[4]),
                "idx_call_long":  to_int(row[5]),
                "idx_call_short": to_int(row[6]),
                "idx_put_long":   to_int(row[7]),
                "idx_put_short":  to_int(row[8]),
            }
    except Exception as e:
        print(f"  [fao-parse] {e}")
    return out


# ── Nifty / VIX (latest day only) ────────────────────────────────
def fetch_nifty_vix():
    s = get_session()
    try:
        r = s.get(ALLINDICES, timeout=20)
        if not r.ok:
            return {}
        data = r.json()
        out = {}
        for item in data.get("data", []):
            idx = item.get("index", "")
            if idx == "NIFTY 50":
                out["nifty_price"]      = float(item.get("last", 0) or 0)
                out["nifty_change"]     = float(item.get("variation", 0) or 0)
                out["nifty_change_pct"] = float(item.get("percentChange", 0) or 0)
            elif idx == "INDIA VIX":
                out["vix_price"]      = float(item.get("last", 0) or 0)
                out["vix_change_pct"] = float(item.get("percentChange", 0) or 0)
        return out
    except Exception as e:
        print(f"  [nifty-vix] {e}")
        return {}


# ── Repo fallback (read our own published history) ───────────────
def fetch_repo_history():
    try:
        r = py_requests.get(REPO_HISTORY_URL, timeout=15)
        if r.ok:
            return {item["date"]: item for item in r.json()}
    except Exception as e:
        print(f"  [repo-fallback] {e}")
    return {}


# ── Empty entry template ─────────────────────────────────────────
def _blank_entry(date_str: str) -> dict:
    return {
        "date": date_str,
        "fii_buy": 0, "fii_sell": 0, "fii_net": 0,
        "dii_buy": 0, "dii_sell": 0, "dii_net": 0,
        "fii_idx_fut_long": 0, "fii_idx_fut_short": 0, "fii_idx_fut_net": 0,
        "dii_idx_fut_long": 0, "dii_idx_fut_short": 0, "dii_idx_fut_net": 0,
        "fii_stk_fut_long": 0, "fii_stk_fut_short": 0, "fii_stk_fut_net": 0,
        "dii_stk_fut_long": 0, "dii_stk_fut_short": 0, "dii_stk_fut_net": 0,
        "fii_idx_call_long": 0, "fii_idx_call_short": 0, "fii_idx_call_net": 0,
        "fii_idx_put_long":  0, "fii_idx_put_short":  0, "fii_idx_put_net":  0,
        "pcr": 1.0, "sentiment_score": 50,
    }


def _has_cash(e: dict) -> bool:
    return (e.get("fii_buy", 0) or 0) > 0 or (e.get("dii_buy", 0) or 0) > 0


def _has_fao(e: dict) -> bool:
    return (e.get("fii_idx_fut_long", 0) or 0) > 0 or (e.get("dii_idx_fut_long", 0) or 0) > 0


def _has_nifty(e: dict) -> bool:
    return (e.get("nifty_price", 0) or 0) > 0


def _apply_cash(entry: dict, cash_rows) -> bool:
    """Apply NSE cash rows for entry['date']. Returns True if updated."""
    if not cash_rows:
        return False
    rows = json.loads(cash_rows) if isinstance(cash_rows, str) else cash_rows
    if not isinstance(rows, list):
        return False
    target = entry["date"]
    updated = False
    for row in rows:
        if row.get("date") != target:
            continue
        cat = (row.get("category", "") or "").upper()
        if "FII" in cat or "FPI" in cat:
            entry["fii_buy"]  = float(row.get("buyValue", 0) or 0)
            entry["fii_sell"] = float(row.get("sellValue", 0) or 0)
            entry["fii_net"]  = float(row.get("netValue", 0) or 0)
            updated = True
        elif "DII" in cat:
            entry["dii_buy"]  = float(row.get("buyValue", 0) or 0)
            entry["dii_sell"] = float(row.get("sellValue", 0) or 0)
            entry["dii_net"]  = float(row.get("netValue", 0) or 0)
            updated = True
    return updated


def _apply_fao(entry: dict, fao_csv: str) -> bool:
    """Apply FAO participant CSV. Returns True if updated."""
    fao = parse_fao_csv(fao_csv)
    if not fao:
        return False
    updated = False
    if "FII" in fao:
        f = fao["FII"]
        entry.update({
            "fii_idx_fut_long":  f["idx_fut_long"],
            "fii_idx_fut_short": f["idx_fut_short"],
            "fii_idx_fut_net":   f["idx_fut_long"] - f["idx_fut_short"],
            "fii_stk_fut_long":  f["stk_fut_long"],
            "fii_stk_fut_short": f["stk_fut_short"],
            "fii_stk_fut_net":   f["stk_fut_long"] - f["stk_fut_short"],
            "fii_idx_call_long":  f["idx_call_long"],
            "fii_idx_call_short": f["idx_call_short"],
            "fii_idx_call_net":   f["idx_call_long"] - f["idx_call_short"],
            "fii_idx_put_long":   f["idx_put_long"],
            "fii_idx_put_short":  f["idx_put_short"],
            "fii_idx_put_net":    f["idx_put_long"] - f["idx_put_short"],
            "pcr": (
                round(f["idx_put_short"] / f["idx_call_short"], 2)
                if f["idx_call_short"] > 0 else 1.0
            ),
        })
        sentiment = 50 + (entry.get("fii_net", 0) / 200) + (entry["fii_idx_fut_net"] / 5000)
        if entry["pcr"] > 1.3:
            sentiment -= 10
        if entry["pcr"] < 0.7:
            sentiment += 10
        entry["sentiment_score"] = round(min(100, max(0, sentiment)), 1)
        updated = True

    if "DII" in fao:
        d = fao["DII"]
        entry.update({
            "dii_idx_fut_long":  d["idx_fut_long"],
            "dii_idx_fut_short": d["idx_fut_short"],
            "dii_idx_fut_net":   d["idx_fut_long"] - d["idx_fut_short"],
            "dii_stk_fut_long":  d["stk_fut_long"],
            "dii_stk_fut_short": d["stk_fut_short"],
            "dii_stk_fut_net":   d["stk_fut_long"] - d["stk_fut_short"],
        })
        updated = True
    return updated


# ── Main ─────────────────────────────────────────────────────────
def main():
    DATA_DIR.mkdir(exist_ok=True)
    print("=== NSE FII/DII Resilient Scraper ===")
    print(f"Run time (UTC): {datetime.now(timezone.utc).isoformat()}")

    # 1. Load existing history → {date: entry}
    history_map: dict[str, dict] = {}
    if HISTORY_FILE.exists():
        try:
            for item in json.loads(HISTORY_FILE.read_text()):
                history_map[item["date"]] = item
        except Exception as e:
            print(f"  [load] Failed to parse existing history: {e}")
    print(f"  Loaded {len(history_map)} existing entries from {HISTORY_FILE}")

    # 2. Pull live cash for the most recent trading day
    print("\n[1/4] Fetching latest NSE cash data...")
    raw_cash_latest = fetch_nse_cash_latest()
    latest_date_str = None
    if raw_cash_latest:
        latest_date_str = next(
            (r.get("date") for r in raw_cash_latest if "FII" in (r.get("category", "") or "").upper()),
            None,
        )
        if latest_date_str:
            print(f"  Latest available trading day: {latest_date_str}")
        else:
            print("  [warn] Live response had no FII row")
    else:
        print("  [warn] Live cash fetch returned nothing")

    # 3. Determine date window to backfill
    today = datetime.now()
    end_dt = parse_dt(latest_date_str) if latest_date_str else today
    start_dt = end_dt - timedelta(days=BACKFILL_DAYS)
    print(f"\n[2/4] Backfill window: {fmt_dt(start_dt)} -> {fmt_dt(end_dt)}")

    # 4. Repo fallback (used only when both live + archive yield nothing)
    print("  Loading repo-fallback history snapshot...")
    repo_map = fetch_repo_history()
    print(f"  Repo fallback contains {len(repo_map)} entries")

    # 5. Walk each weekday in the window
    cur = start_dt
    days_to_process = []
    while cur <= end_dt:
        if is_trading_day(cur):
            days_to_process.append(fmt_dt(cur))
        cur += timedelta(days=1)
    print(f"  {len(days_to_process)} trading days in scan window")

    # 6. For each day, fill missing branches independently
    print("\n[3/4] Backfilling missing data per day...")
    cash_filled = fao_filled = added = 0
    for ds in days_to_process:
        entry = history_map.get(ds, _blank_entry(ds))
        before_cash = _has_cash(entry)
        before_fao  = _has_fao(entry)

        # ── Cash branch ──
        if not before_cash:
            applied = False
            # (a) live snapshot for the latest day
            if ds == latest_date_str and raw_cash_latest:
                applied = _apply_cash(entry, raw_cash_latest)
            # (b) NSE archive for older days
            if not applied:
                d = parse_dt(ds)
                arch = fetch_nse_archive(d, d)
                if arch:
                    applied = _apply_cash(entry, arch)
                    if applied:
                        print(f"    [cash] {ds} <- NSE archive")
            # (c) repo fallback
            if not applied and ds in repo_map:
                src = repo_map[ds]
                if src.get("fii_buy", 0) > 0 or src.get("dii_buy", 0) > 0:
                    for k in ("fii_buy", "fii_sell", "fii_net", "dii_buy", "dii_sell", "dii_net"):
                        v = src.get(k, 0) or 0
                        if v:
                            entry[k] = v
                    applied = True
                    print(f"    [cash] {ds} <- repo fallback")
            if applied:
                cash_filled += 1

        # ── FAO branch (always attempt if missing) ──
        if not _has_fao(entry):
            csv_text = fetch_fao_csv(ds)
            if csv_text and _apply_fao(entry, csv_text):
                fao_filled += 1
                print(f"    [fao]  {ds} <- NSE archives")
            elif ds in repo_map and _has_fao(repo_map[ds]):
                src = repo_map[ds]
                for k, v in src.items():
                    if k.endswith(("_long", "_short", "_net")) or k in ("pcr", "sentiment_score"):
                        if v:
                            entry[k] = v
                print(f"    [fao]  {ds} <- repo fallback")
                fao_filled += 1

        # ── Nifty/VIX (only for the latest day) ──
        if ds == latest_date_str and not _has_nifty(entry):
            entry.update(fetch_nifty_vix())

        new_cash = _has_cash(entry)
        new_fao  = _has_fao(entry)
        if new_cash != before_cash or new_fao != before_fao or ds == latest_date_str:
            entry["fetched_at"] = datetime.now(timezone.utc).isoformat()

        if ds not in history_map:
            added += 1
        history_map[ds] = entry

    print(f"\n  Filled cash for {cash_filled} day(s), FAO for {fao_filled} day(s), added {added} new day(s)")

    # 7. Persist
    print("\n[4/4] Writing output files...")
    history = sorted(history_map.values(), key=lambda x: parse_dt(x["date"]), reverse=True)
    history = history[:MAX_HISTORY]
    HISTORY_FILE.write_text(json.dumps(history, indent=2))
    if history:
        LATEST_FILE.write_text(json.dumps(history[0], indent=2))
        print(f"  Latest entry: {history[0]['date']} (cash={_has_cash(history[0])}, fao={_has_fao(history[0])})")
    print(f"  Wrote {len(history)} entries to {HISTORY_FILE}")

    # 8. Summary
    missing_cash = [e["date"] for e in history if not _has_cash(e)]
    missing_fao  = [e["date"] for e in history if not _has_fao(e)]
    if missing_cash:
        print(f"  Still missing cash data ({len(missing_cash)}): {missing_cash[:10]}...")
    if missing_fao:
        print(f"  Still missing FAO data  ({len(missing_fao)}): {missing_fao[:10]}...")
    print("=== DONE ===")


if __name__ == "__main__":
    sys.exit(main() or 0)
