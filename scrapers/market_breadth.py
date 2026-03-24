"""
Market Breadth scraper using Scrapling (anki1007/webscrap).
Fetches live advance/decline, gainers/losers, volume/delivery leaders,
52-week high/lows, sector performance, and block deals from NSE India.
"""

from __future__ import annotations
import logging
import threading
import time
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ── Cache ──────────────────────────────────────────────────────
_cache: Dict[str, Any] = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 120  # 2 minutes cache for high frequency breadth 

def _is_fresh(key: str) -> bool:
    with _cache_lock:
        if key not in _cache:
            return False
        return (time.time() - _cache[key]["ts"]) < _CACHE_TTL

def _set(key: str, data: Any):
    with _cache_lock:
        _cache[key] = {"data": data, "ts": time.time()}

def _get(key: str) -> Any:
    with _cache_lock:
        return _cache.get(key, {}).get("data")

# ── NSE Session ───────────────────────────────────────────────
_fetcher = None
_fetcher_lock = threading.Lock()
_last_session_ts = 0.0
_SESSION_TTL = 300
_session_failed = False  # force reset if last call returned empty data

_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
    "Referer": "https://www.nseindia.com/",
    "X-Requested-With": "XMLHttpRequest",
    "DNT": "1",
    "Connection": "keep-alive",
}

def _get_fetcher():
    global _fetcher, _last_session_ts, _session_failed
    with _fetcher_lock:
        stale = (time.time() - _last_session_ts) > _SESSION_TTL
        if _fetcher is None or stale or _session_failed:
            _session_failed = False
            try:
                from scrapling.fetchers import Fetcher
                f = Fetcher(impersonate="chrome124", auto_match=False)
                f.get(
                    "https://www.nseindia.com",
                    headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                             "Accept-Language": "en-IN,en;q=0.9", "DNT": "1"},
                    timeout=20,
                )
                _fetcher = f
                _last_session_ts = time.time()
                time.sleep(0.5)
            except Exception as e:
                logger.warning(f"NSE scrapling session failed: {e}")
                _fetcher = None
    return _fetcher


def _nse_get_requests(path: str) -> Any:
    """Fallback: plain requests.Session with cookie priming."""
    import requests
    s = requests.Session()
    s.headers.update(_NSE_HEADERS)
    # Prime cookies
    s.get("https://www.nseindia.com", timeout=15)
    time.sleep(0.5)
    resp = s.get(f"https://www.nseindia.com{path}", timeout=15)
    resp.raise_for_status()
    return resp.json()


def _nse_get(path: str) -> Any:
    global _session_failed
    url = f"https://www.nseindia.com{path}"
    f = _get_fetcher()
    if f is not None:
        try:
            resp = f.get(url, headers=_NSE_HEADERS, timeout=15)
            data = resp.json()
            if data:
                return data
            # Empty response — mark session as failed so it resets next call
            _session_failed = True
        except Exception as e:
            logger.warning(f"Scrapling NSE get failed ({path}): {e}")
            _session_failed = True
    # Fallback to plain requests
    try:
        return _nse_get_requests(path)
    except Exception as e:
        logger.warning(f"requests NSE get also failed ({path}): {e}")
        return None

# ── Fetchers ───────────────────────────────────────────────────

def get_market_breadth() -> Dict[str, Any]:
    if _is_fresh("market_breadth"):
        return _get("market_breadth")
    
    data = {}
    
    try:
        # Market Status (Advances/Declines/Unchanged)
        status = _nse_get("/api/marketStatus")
        if status and "marketState" in status:
            cm = next((m for m in status["marketState"] if m.get("market") == "Capital Market"), None)
            if cm and "index" in cm:
                adv = cm.get("advances", 0)
                dec = cm.get("declines", 0)
                unc = cm.get("unchanged", 0)
                data["advances"] = adv
                data["declines"] = dec
                data["unchanged"] = unc
                data["ad_ratio"] = round(adv / dec, 2) if dec > 0 else 99.99
        
        # Fallback to NIFTY 500 breadth if MarketStatus lacks it
        if "advances" not in data:
            n500 = _nse_get("/api/equity-stockIndices?index=NIFTY%20500")
            if n500 and "advance" in n500:
                data["advances"] = int(n500["advance"].get("advances", 0))
                data["declines"] = int(n500["advance"].get("declines", 0))
                data["unchanged"] = int(n500["advance"].get("unchanged", 0))
                data["ad_ratio"] = round(data["advances"] / data["declines"], 2) if data["declines"] > 0 else 99.99

        _set("market_breadth", data)
    except Exception as e:
        logger.error(f"Breadth fetch error: {e}")
        
    return data or {"advances": 0, "declines": 0, "unchanged": 0, "ad_ratio": 0}

def get_top_movers() -> Dict[str, Any]:
    if _is_fresh("top_movers"): return _get("top_movers")
    
    data = {"gainers": [], "losers": []}
    try:
        g = _nse_get("/api/live-analysis-variations?index=gainers")
        l = _nse_get("/api/live-analysis-variations?index=losers")
        if g and "NIFTY" in g:
            data["gainers"] = g["NIFTY"].get("data", [])[:15]
        if l and "NIFTY" in l:
            data["losers"] = l["NIFTY"].get("data", [])[:15]
        
        _set("top_movers", data)
    except Exception as e:
        logger.error(f"Top movers fetch error: {e}")
    return data

def get_volume_leaders() -> Dict[str, Any]:
    if _is_fresh("volume_leaders"): return _get("volume_leaders")
    data = {"volume": [], "delivery": []}
    try:
        v = _nse_get("/api/live-analysis-volume-gainers")
        if v and isinstance(v, list):
            # Sort by absolute volume
            vol_sorted = sorted(v, key=lambda x: x.get("volume", 0), reverse=True)
            data["volume"] = vol_sorted[:15]
            
            # Sort by delivery volume % if available, otherwise by robust logic
            # NSE provides delivery data sometimes. If not, fallback to pure volume.
            # E.g., highest delivery total volume.
            # Assuming 'deliveryToTradedQuantity' or similar field exists, or we just pull the top 15 list 
            del_sorted = sorted(v, key=lambda x: x.get("deliveryToTradedQuantity", x.get("volume", 0)), reverse=True)
            data["delivery"] = del_sorted[:15]
            
        _set("volume_leaders", data)
    except Exception as e:
        logger.error(f"Volume leaders error: {e}")
    return data

def get_52w_high_low() -> Dict[str, Any]:
    if _is_fresh("52w"): return _get("52w")
    data = {"high": [], "low": []}
    try:
        h = _nse_get("/api/live-analysis-52Week?index=high")
        l = _nse_get("/api/live-analysis-52Week?index=low")
        if h and "data" in h: data["high"] = h["data"][:15]
        if l and "data" in l: data["low"] = l["data"][:15]
        _set("52w", data)
    except Exception as e:
        logger.error(f"52W fetch error: {e}")
    return data

def get_sector_performance() -> List[Dict[str, Any]]:
    if _is_fresh("sectors"): return _get("sectors")
    sectors = []
    try:
        indices = _nse_get("/api/allIndices")
        if indices and "data" in indices:
            for item in indices["data"]:
                name = item.get("indexSymbol", "")
                if name.startswith("NIFTY "):
                    sectors.append({
                        "name": name,
                        "key": item.get("key", "OTHER"),
                        "ltp": item.get("last", 0),
                        "change": item.get("variation", 0),
                        "pChange": item.get("percentChange", 0),
                        "advances": item.get("advances", 0),
                        "declines": item.get("declines", 0)
                    })
            # sort broadly by name instead of just percent for grouping stability
            sectors.sort(key=lambda x: x["pChange"], reverse=True)
            _set("sectors", sectors)
    except Exception as e:
        logger.error(f"Sectors fetch error: {e}")
    return sectors

def get_block_deals() -> List[Dict[str, Any]]:
    if _is_fresh("block_deals"): return _get("block_deals")
    deals = []
    try:
        b = _nse_get("/api/block-deal")
        if b and "data" in b:
            deals = b["data"][:25]
        _set("block_deals", deals)
    except Exception as e:
        logger.error(f"Block deals error: {e}")
    return deals

def get_historical_ad() -> List[Dict[str, Any]]:
    if _is_fresh("historical_ad"): return _get("historical_ad")
    data = []
    try:
        j = _nse_get("/api/historical/advances-declines")
        if j and isinstance(j, list):
            data = j[:10]  # Get last 10 days of A/D data
        _set("historical_ad", data)
    except Exception as e:
        logger.error(f"Historical A/D error: {e}")
    return data

def get_volume_spurts() -> List[Dict[str, Any]]:
    if _is_fresh("volume_spurts"): return _get("volume_spurts")
    data = []
    try:
        j = _nse_get("/api/live-analysis-volume-spurts")
        if j and "data" in j:
            # Grab top 15 volume spurts 
            for item in j["data"][:15]:
                data.append({
                    "symbol": item.get("symbol", ""),
                    "ltp": item.get("lastPrice", 0),
                    "pChange": item.get("pChange", 0),
                    "volumeSpurt": item.get("volSpurt", 0), 
                    "volume": item.get("volume", 0)
                })
        _set("volume_spurts", data)
    except Exception as e:
        logger.error(f"Volume spurts error: {e}")
    return data

def get_all_market_breadth() -> Dict[str, Any]:
    """Aggregate all breadth modules into one massive JSON payload sequentially."""
    result = {}
    try:
        result["breadth"] = get_market_breadth()
        result["movers"] = get_top_movers()
        result["volume"] = get_volume_leaders()
        result["fifty_two"] = get_52w_high_low()
        result["sectors"] = get_sector_performance()
        result["blocks"] = get_block_deals()
        result["historical_ad"] = get_historical_ad()
        result["spurts"] = get_volume_spurts()
    except Exception as e:
        logger.error(f"Sequential fetch error: {e}")
    
    return result
