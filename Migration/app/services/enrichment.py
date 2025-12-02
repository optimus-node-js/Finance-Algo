import os
import json
import re
import uuid
import time
import requests
import threading
import concurrent.futures
from tqdm import tqdm
from collections import defaultdict
from sqlalchemy import MetaData, Table, insert

from app.core.config import settings
from app.core.logging import log_message, bulk_log_to_db
from app.db.session import get_db_engines

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
def enrich_stocks_with_tokens(create_json_files=True, input_data=None):
    """
    Fetches Angel One Tokens and other details using the Angel Search API.
    """
    JSON_PATH = os.path.join(settings.DATA_DIR, 'NSE_STOCKS_SKELETON.json')
    MAX_WORKERS = 5 
    MISSING_LOG_PATH = os.path.join(settings.LOG_DIR, 'angel_missing_stocks.log')

    if not os.path.exists(settings.LOG_DIR): os.makedirs(settings.LOG_DIR)

    # Load cookie from settings (.env file)
    COOKIE_STRING = settings.ANGEL_COOKIE 

    HEADERS_TEMPLATE = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
        "Referer": "https://www.angelone.in/",
        "Origin": "https://www.angelone.in",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "App-Version": "sparkweb1",
        "Applicationname": "Spark-Web",
        "X-Sourceid": "3",
        "X-Tokentype": "non_trade_access_token",
        "X-Build-Number": "1",
        "X-Deviceid": "cb063c0f-d478-5a2d-b803-4beaa4bc7166", 
        "X-Clientpublicip": "2409:40c4:4b:22b5:1c92:1114:bc1b:4e59",
        "Cookie": COOKIE_STRING
    }

    thread_local = threading.local()

    def get_session():
        if not hasattr(thread_local, "session"):
            thread_local.session = requests.Session()
            thread_local.session.headers.update(HEADERS_TEMPLATE)
        return thread_local.session

    def log_missing(company_name, tried_queries, reason):
        with open(MISSING_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(f"MISSING: {company_name} | Queries: {tried_queries} | Reason: {reason}\\n")

    def clean_company_name(name):
        cleaned = re.sub(r'\s+\(?(Limited|Ltd|India|LTD|LIMITED|INDIA|Plc|Corp|Corporation)\)?\.?$', '', name, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s+\([^)]*\)', '', cleaned)
        return cleaned.strip()

    def perform_search(keyword, log_buffer=None):
        session = get_session()
        headers = session.headers.copy()
        headers["X-Requestid"] = str(uuid.uuid4())
        SEARCH_URL = "https://global-search-bbe-prod.angelone.in/v5/search"
        params = {"userType": "B2C", "accountType": "D", "keyword": keyword}
        
        try:
            if log_buffer is not None:
                log_buffer.append({
                    "level": "INFO", "source": "angel_api", "event": "search",
                    "message": f"Searching: {keyword}", "context": {"url": SEARCH_URL, "keyword": keyword}
                })
            response = session.get(SEARCH_URL, headers=headers, params=params, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success" and "data" in data and "dataList" in data["data"]:
                    return data["data"]["dataList"]
        except Exception:
            pass
        return []

    def parse_best_match(results, search_symbol=None):
        if not results: return None
        if search_symbol:
            for item in results:
                if item.get("type") == "scrip" and item.get("symbolName") == search_symbol:
                    return item
        for item in results:
            if item.get("type") == "scrip":
                if item.get("scripType") == "nseCash" and item.get("series") == "EQ":
                    return item
                if item.get("scripType") == "nseCash" and item.get("tradeSymbol", "").endswith("-EQ"):
                    return item
        return None

    def fetch_and_enrich_stock(stock_obj):
        local_logs = []
        if stock_obj["trading_info"].get("token_nse") and stock_obj["__RAW_DATA_SOURCES__"].get("source_angel_api"):
            return stock_obj, local_logs

        identifiers = stock_obj.get("identifiers", {})
        symbol = identifiers.get("symbol")
        company_name = identifiers.get("company_name", "").strip()
        if not symbol or not company_name: return stock_obj, local_logs

        match = None
        tried_queries = []

        search_query = symbol
        tried_queries.append(search_query)
        results = perform_search(search_query, log_buffer=local_logs)
        match = parse_best_match(results, search_symbol=symbol)

        if not match:
            search_query = company_name
            tried_queries.append(search_query)
            results = perform_search(search_query, log_buffer=local_logs)
            match = parse_best_match(results, search_symbol=symbol)

        if not match:
            cleaned_name = clean_company_name(company_name)
            if cleaned_name != company_name:
                search_query = cleaned_name
                tried_queries.append(search_query)
                results = perform_search(search_query, log_buffer=local_logs)
                match = parse_best_match(results, search_symbol=symbol)

        if match:
            stock_obj["__RAW_DATA_SOURCES__"]["source_angel_api"] = match
            stock_obj["trading_info"]["token_nse"] = match.get("nseCashToken")
            stock_obj["trading_info"]["token_bse"] = match.get("bseCashToken")
            stock_obj["trading_info"]["tick_size"] = match.get("priceTck")
            if match.get("regLot"):
                try: stock_obj["trading_info"]["lot_size"] = int(match.get("regLot"))
                except: pass
            stock_obj["classification"]["sector"] = match.get("sector")
            stock_obj["classification"]["market_cap_category"] = match.get("marketCapCategory")
            stock_obj["classification"]["market_cap_value"] = match.get("mktcap")
            if match.get("isMTFEligible") is not None:
                stock_obj["flags"]["is_mtf_eligible"] = match.get("isMTFEligible")
        else:
            log_missing(company_name, tried_queries, "No match found")

        return stock_obj, local_logs

    master_list = []
    if input_data:
        master_list = input_data
        log_message(f"🔄 Loaded {len(master_list)} stocks from memory.")
    elif os.path.exists(JSON_PATH):
        log_message(f"🔄 Loading Master JSON from {JSON_PATH}...")
        with open(JSON_PATH, 'r', encoding='utf-8') as f:
            master_list = json.load(f)
    else:
        log_message(f"❌ Error: No input data and {JSON_PATH} not found.")
        return []

    if create_json_files:
        open(MISSING_LOG_PATH, 'w').close()

    pending = [s for s in master_list if not s["trading_info"].get("token_nse")]
    log_message(f"🕵️ Sweeping {len(pending)} missing stocks...")
    
    if len(pending) == 0:
        log_message("✅ No missing stocks to process!")
        return master_list

    updated_list = []
    all_logs = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = list(tqdm(executor.map(fetch_and_enrich_stock, master_list), total=len(master_list)))
        for stock, logs in results:
            updated_list.append(stock)
            all_logs.extend(logs)

    if all_logs:
        log_message(f"📝 Bulk inserting {len(all_logs)} API logs to DB...")
        bulk_log_to_db(get_db_engines(), all_logs)

    success_count = sum(1 for s in updated_list if s["trading_info"].get("token_nse"))

    if create_json_files:
        log_message(f"💾 Saving...")
        with open(JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(updated_list, f, indent=4)
        log_message(f"🔹 Check logs at: {MISSING_LOG_PATH}")

    log_message(f"✅ COMPLETE. Total Tokens Found: {success_count}")
    return updated_list
