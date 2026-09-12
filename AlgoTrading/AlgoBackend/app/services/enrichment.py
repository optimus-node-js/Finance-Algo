import os
import json
import requests
from tqdm import tqdm
from collections import defaultdict

from app.core.config import settings
from app.core.logging import log_message

# ==========================================
# 1. LOCAL ENRICHER (Cell 6)
# ==========================================
def enrich_stocks_from_indices(create_json_files=True, input_data=None, indices_data=None):
    """
    Enriches the Master Stock List using the fetched Index Data (Reverse Lookup).
    """
    MASTER_FILE = os.path.join(settings.DATA_DIR, 'NSE_STOCKS_SKELETON.json')
    INDICES_DIR = os.path.join(settings.DATA_DIR, 'indices')

    def build_indices_lookup():
        log_message(f"📖 Scanning Index Data to build Lookup Map...")
        lookup_map = defaultdict(lambda: {"meta": None, "indices": []})
        
        if indices_data:
            source_items = indices_data.items()
            log_message(f"🔹 Using {len(source_items)} indices from memory.")
        elif os.path.exists(INDICES_DIR):
            files = [f for f in os.listdir(INDICES_DIR) if f.endswith('.json')]
            source_items = []
            for filename in tqdm(files, desc="Scanning Files"):
                index_name = filename.replace('.json', '').replace('_', ' ')
                file_path = os.path.join(INDICES_DIR, filename)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = json.load(f)
                    source_items.append((index_name, content))
                except Exception:
                    continue
        else:
            log_message(f"❌ Error: No indices data found (Memory or Disk).")
            return {}

        for index_name, content in source_items:
            if isinstance(content, dict) and "data" in content:
                stock_list = content["data"]
            elif isinstance(content, list):
                stock_list = content
            else:
                continue

            for stock in stock_list:
                if not isinstance(stock, dict): continue
                symbol = stock.get("symbol")
                if not symbol: continue
                
                lookup_map[symbol]["indices"].append(index_name)
                
                new_meta = stock.get("meta")
                current_meta = lookup_map[symbol]["meta"]
                
                if new_meta:
                    if current_meta is None:
                        lookup_map[symbol]["web_data"] = stock
                        lookup_map[symbol]["meta"] = new_meta
                    elif new_meta.get("industry") and not current_meta.get("industry"):
                        lookup_map[symbol]["web_data"] = stock
                        lookup_map[symbol]["meta"] = new_meta
        return lookup_map

    def update_master_file(lookup_map):
        master_list = []
        if input_data:
            master_list = input_data
            log_message(f"🔄 Loaded {len(master_list)} stocks from memory.")
        elif os.path.exists(MASTER_FILE):
            log_message(f"🔄 Loading Master JSON from {MASTER_FILE}...")
            with open(MASTER_FILE, 'r', encoding='utf-8') as f:
                master_list = json.load(f)
        else:
            log_message(f"❌ Error: No input data and {MASTER_FILE} not found.")
            return []

        log_message(f"⚡ Enriching fields...")
        enriched_count = 0
        
        for stock_obj in tqdm(master_list, desc="Updating Stocks"):
            symbol = stock_obj["master_key"]
            if symbol in lookup_map:
                match = lookup_map[symbol]
                web_data = match.get("web_data")
                indices_list = match.get("indices")
                meta = match.get("meta", {})
                
                stock_obj["__RAW_DATA_SOURCES__"]["source_nse_web"] = web_data
                if meta.get("industry"):
                    stock_obj["classification"]["industry"] = meta.get("industry")
                if meta.get("slb_isin"):
                    stock_obj["identifiers"]["slb_isin"] = meta.get("slb_isin")
                if meta.get("activeSeries"):
                    stock_obj["identifiers"]["active_series"] = meta.get("activeSeries")

                stock_obj["flags"]["is_fno"] = meta.get("isFNOSec", False)
                stock_obj["flags"]["is_etf"] = meta.get("isETFSec", False)
                stock_obj["flags"]["is_slb_eligible"] = meta.get("isSLBSec", False)
                stock_obj["flags"]["is_suspended"] = meta.get("isSuspended", False)
                stock_obj["flags"]["is_delisted"] = meta.get("isDelisted", False)
                stock_obj["flags"]["is_municipal_bond"] = meta.get("isMunicipalBond", False)
                stock_obj["flags"]["is_mtf_eligible"] = False 
                
                stock_obj["relations"] = {"indices": indices_list}
                enriched_count += 1
            else:
                stock_obj["relations"] = {"indices": []}

        if create_json_files:
            log_message(f"💾 Saving updates to {MASTER_FILE}...")
            with open(MASTER_FILE, 'w', encoding='utf-8') as f:
                json.dump(master_list, f, indent=4)
            log_message(f"🔹 File Updated: {MASTER_FILE}")
        else:
            log_message(f"💾 Updates applied in-memory.")

        log_message(f"✅ UPDATE COMPLETE. Enriched: {enriched_count}/{len(master_list)}")
        return master_list

    lookup_map = build_indices_lookup()
    if lookup_map:
        return update_master_file(lookup_map)
    return []

# ==========================================
# 2. TOKEN HUNTER (Cell 7)
# ==========================================
SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"


def _fetch_scrip_master_lookup() -> dict:
    """Angel One's official, public, unauthenticated instrument master --
    every token across all exchanges/segments. Verified live: NSE cash-equity
    rows have exch_seg="NSE", instrumenttype="", symbol like "RELIANCE-EQ",
    and the "name" field is the clean trading symbol (e.g. "RELIANCE"),
    matching what NSE's own equity list calls SYMBOL.

    Replaces the old cookie-based angelone.in website search entirely -- that
    approach needed a manually-refreshed browser cookie (ANGEL_COOKIE) that
    expires and silently breaks token lookups. This needs no auth, no cookie,
    no rate limit, and is the same data Angel One's own docs point SDK users
    to for building a local instrument master.
    """
    resp = requests.get(SCRIP_MASTER_URL, timeout=60)
    resp.raise_for_status()
    lookup = {}
    for r in resp.json():
        if r.get("exch_seg") == "NSE" and r.get("instrumenttype") == "" and r.get("symbol", "").endswith("-EQ"):
            lookup[r["name"]] = {
                "token_nse": r["token"],
                "angel_symbol": r["symbol"],  # e.g. "RELIANCE-EQ" -- what instruments.symbol should store
                "tick_size": float(r.get("tick_size", 5) or 5) / 100,  # paise -> rupees
                "lot_size": int(r.get("lotsize", 1) or 1),
            }
    return lookup


def enrich_stocks_with_tokens(create_json_files=True, input_data=None):
    """
    Fetches Angel One tokens via the official scrip master file (see
    _fetch_scrip_master_lookup) instead of the old fragile cookie-scraped
    website search.
    """
    JSON_PATH = os.path.join(settings.DATA_DIR, 'NSE_STOCKS_SKELETON.json')

    master_list = []
    if input_data:
        master_list = input_data
        log_message(f"Loaded {len(master_list)} stocks from memory.")
    elif os.path.exists(JSON_PATH):
        log_message(f"Loading Master JSON from {JSON_PATH}...")
        with open(JSON_PATH, 'r', encoding='utf-8') as f:
            master_list = json.load(f)
    else:
        log_message(f"Error: No input data and {JSON_PATH} not found.")
        return []

    log_message("Downloading Angel One OpenAPIScripMaster.json...")
    lookup = _fetch_scrip_master_lookup()
    log_message(f"{len(lookup)} NSE cash-equity tokens indexed.")

    matched = 0
    for stock_obj in master_list:
        symbol = stock_obj.get("identifiers", {}).get("symbol")
        match = lookup.get(symbol)
        if not match:
            continue
        stock_obj["trading_info"]["token_nse"] = match["token_nse"]
        stock_obj["trading_info"]["angel_symbol"] = match["angel_symbol"]
        stock_obj["trading_info"]["tick_size"] = match["tick_size"]
        stock_obj["trading_info"]["lot_size"] = match["lot_size"]
        matched += 1

    if create_json_files:
        with open(JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(master_list, f, indent=4)

    log_message(f"COMPLETE. Total Tokens Found: {matched}/{len(master_list)}")
    return master_list
