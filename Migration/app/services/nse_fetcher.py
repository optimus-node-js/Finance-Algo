import os
import json
import time
import random
import requests
import io
import pandas as pd
import concurrent.futures
from tqdm import tqdm
from sqlalchemy import text

from app.core.config import settings
from app.core.logging import log_message
from app.db.session import get_db_engines

# ==========================================
# 1. FETCH NSE EQUITY LIST (Cell 3)
# ==========================================
def fetch_nse_equity_list(create_json_files=True):
    """
    Fetches the official NSE Equity List (CSV) and builds the master skeleton.
    """
    NSE_CSV_URLS = [
        "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
        "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
        "https://www.nseindia.com/content/equities/EQUITY_L.csv"
    ]
    OUTPUT_FILE = os.path.join(settings.DATA_DIR, 'NSE_STOCKS_SKELETON.json')

    if not os.path.exists(settings.DATA_DIR):
        os.makedirs(settings.DATA_DIR)

    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Connection': 'keep-alive'
    }

    def fetch_csv():
        log_message(f"📥 Downloading NSE Equity CSV...")
        
        for url in NSE_CSV_URLS:
            log_message(f"🌐 Trying URL: {url}")
            try:
                response = requests.get(url, headers=HEADERS, timeout=15)
                if response.status_code == 200:
                    raw_text = response.content.decode('utf-8')
                    csv_content = io.StringIO(raw_text)
                    df = pd.read_csv(csv_content, keep_default_na=False)
                    df.columns = [c.strip() for c in df.columns]
                    log_message(f"✅ Downloaded & Parsed Rows: {len(df)}")
                    return df
                else:
                    log_message(f"❌ Failed {url}. Status: {response.status_code}")
            except Exception as e:
                log_message(f"❌ Exception {url}: {e}")
        
        log_message("❌ All NSE CSV URLs failed.")
        return None

    def build_skeleton(df):
        master_list = []
        log_message(f"🏗️ Building Master JSON Structure...")
        
        for _, row in df.iterrows():
            symbol = str(row.get('SYMBOL', '')).strip()
            series = str(row.get('SERIES', '')).strip()
            name = str(row.get('NAME OF COMPANY', '')).strip()
            isin = str(row.get('ISIN NUMBER', '')).strip()
            listing_date = str(row.get('DATE OF LISTING', '')).strip()
            
            try:
                mkt_lot = int(row.get('MARKET LOT', 1))
            except:
                mkt_lot = 1
                
            stock_obj = {
                "master_key": symbol,
                "identifiers": {
                    "symbol": symbol,
                    "isin": isin,
                    "slb_isin": None,
                    "company_name": name,
                    "active_series": [series] 
                },
                "trading_info": {
                    "token_nse": None,
                    "token_bse": None,
                    "tick_size": None,
                    "lot_size": mkt_lot, 
                    "listing_date": listing_date,
                    "currency": "INR",
                    "segment": "EQUITY"
                },
                "classification": {
                    "industry": None,
                    "sector": None,
                    "market_cap_category": None,
                    "market_cap_value": None
                },
                "flags": {
                    "is_fno": False,
                    "is_etf": False,
                    "is_slb_eligible": False,
                    "is_mtf_eligible": False,
                    "is_suspended": False,
                    "is_delisted": False,
                    "is_debt": False,
                    "is_municipal_bond": False
                },
                "__RAW_DATA_SOURCES__": {
                    "source_nse_csv": {
                        "SYMBOL": symbol,
                        "NAME OF COMPANY": name,
                        "SERIES": series,
                        "DATE OF LISTING": listing_date,
                        "PAID UP VALUE": row.get('PAID UP VALUE'),
                        "MARKET LOT": row.get('MARKET LOT'),
                        "ISIN NUMBER": isin,
                        "FACE VALUE": row.get('FACE VALUE')
                    },
                    "source_angel_api": None,
                    "source_nse_web": None 
                }
            }
            master_list.append(stock_obj)
        return master_list

    df = fetch_csv()
    if df is not None:
        data = build_skeleton(df)
        if create_json_files:
            with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
            log_message(f"🎉 SKELETON CREATED: {OUTPUT_FILE}")
        else:
            log_message(f"🎉 SKELETON CREATED (In-Memory)")
        return data
    return []

# ==========================================
# 2. FETCH NSE INDICES LIST (Cell 4)
# ==========================================
def fetch_nse_indices_list(create_json_files=True):
    """
    Fetches the list of all NSE Indices and updates the 'indices' table.
    """
    STORE_IN_JSON = create_json_files       
    STORE_IN_POSTGRES = True   
    OUTPUT_FILE = os.path.join(settings.DATA_DIR, 'NSE_Indices_List.json')

    if not os.path.exists(settings.DATA_DIR): os.makedirs(settings.DATA_DIR)

    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://www.nseindia.com/market-data/live-market-indices',
        'Connection': 'keep-alive',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'X-Requested-With': 'XMLHttpRequest'
    }

    URL_HOME = "https://www.nseindia.com"
    URL_API = "https://www.nseindia.com/api/allIndices"

    def save_to_postgres(data_list):
        """Upserts into the REAL `indices` table from sql/schema.sql
        (id, name, token, exchange_id, category, source_url). Deliberately
        does NOT drop/recreate the table -- that used to run an incompatible
        ad-hoc shape (key/index_symbol/last_price/...) and CASCADE-drop
        index_constituents along with it every time this ran."""
        engines = get_db_engines()
        success_count = 0

        for name, engine in engines.items():
            if engine is None: continue
            try:
                with engine.begin() as conn:
                    conn.execute(text(
                        "INSERT INTO exchanges (code, name, currency, timezone) "
                        "VALUES ('NSE', 'National Stock Exchange', 'INR', 'Asia/Kolkata') "
                        "ON CONFLICT (code) DO NOTHING"
                    ))
                    exchange_id = conn.execute(text("SELECT id FROM exchanges WHERE code = 'NSE'")).scalar()

                    for item in data_list:
                        index_name = item.get("index", "").strip()
                        if not index_name:
                            continue
                        conn.execute(text(
                            "INSERT INTO indices (name, exchange_id, category, source_url) "
                            "VALUES (:name, :exchange_id, :category, :source_url) "
                            "ON CONFLICT (name) DO UPDATE SET category = EXCLUDED.category"
                        ), {
                            "name": index_name,
                            "exchange_id": exchange_id,
                            "category": (item.get("key") or "")[:50],
                            "source_url": "https://www.nseindia.com/api/allIndices",
                        })
                log_message(f"[{name.upper()}] indices table upserted: {len(data_list)} records.")
                success_count += 1
            except Exception as e:
                log_message(f"[{name.upper()}] Database Error: {str(e)}")

        return success_count > 0

    def fetch_manual():
        log_message("🚀 Starting Fetch via Manual Session...")
        session = requests.Session()
        session.headers.update(HEADERS)
        try:
            session.get(URL_HOME, timeout=15)
            time.sleep(random.uniform(2, 4))
            response_api = session.get(URL_API, timeout=15)
            
            if response_api.status_code == 200:
                raw_data = response_api.json()
                if isinstance(raw_data, dict) and "data" in raw_data: data_list = raw_data["data"]
                elif isinstance(raw_data, list): data_list = raw_data
                else: return None
                
                processed_list = []
                rename_map = {"pe": "Price-to-Earnings-ratio", "pb": "Price-to-Book-ratio", "dy": "Dividend-Yield"}
                keys_to_remove = ["chartTodayPath", "chart30dPath", "chart365dPath"]

                for item in data_list:
                    if not isinstance(item, dict): continue 
                    category_key = item.get("key", "").upper()
                    item["isDerivativesEligible"] = "INDICES ELIGIBLE IN DERIVATIVES" in category_key
                    for k in keys_to_remove: item.pop(k, None)
                    for old_key, new_key in rename_map.items():
                        if old_key in item: item[new_key] = item.pop(old_key)
                    processed_list.append(item)
                return processed_list
            else:
                log_message(f"⚠️ API Failed: {response_api.status_code}")
                return None
        except Exception as e:
            log_message(f"❌ Exception: {str(e)}")
            return None

    data = fetch_manual()
    if data:
        if STORE_IN_JSON:
            with open(OUTPUT_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)
            log_message(f"✅ Saved JSON: {OUTPUT_FILE}")
        if STORE_IN_POSTGRES:
            save_to_postgres(data)
        return data
    else:
        log_message("❌ FAILURE: Could not fetch data.")
        return []

# ==========================================
# 3b. SECTOR CLASSIFICATION (static archive CSVs -- replaces Cell 5)
# ==========================================
def fetch_sector_classification() -> dict:
    """
    NSE's dynamic site (www.nseindia.com, used by fetch_nse_indices_details
    below) actively blocks non-browser requests -- confirmed live, HTTP 403
    on the homepage itself, so that per-index detail fetch can never succeed
    as written. archives.nseindia.com serves static CSV files and is NOT
    behind that protection (verified live, 200 OK) -- these are the official
    per-index constituent lists NSE itself publishes, with an Industry column.
    Covers ~750 (Nifty Total Market) + ~250 (microcap) of the largest/most
    liquid names -- not the full universe, but the ones that matter most for
    scanning. The rest stay NULL, same forward-compatible pattern used
    elsewhere (e.g. /market/instruments/search).
    """
    urls = [
        "https://archives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv",
        "https://archives.nseindia.com/content/indices/ind_niftymicrocap250_list.csv",
    ]
    sector_map = {}
    for url in urls:
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.content.decode("utf-8")))
            sector_map.update(dict(zip(df["Symbol"], df["Industry"])))
            log_message(f"Sector classification: {len(df)} rows from {url}")
        except Exception as e:
            log_message(f"Sector classification fetch failed for {url}: {e}")
    return sector_map


# ==========================================
# 3. FETCH NSE INDICES DETAILS (Cell 5) -- DEPRECATED, kept for reference.
# NSE's dynamic site blocks non-browser requests (confirmed live, 403 on the
# homepage session-init this depends on). Not called from the seed pipeline
# anymore -- see fetch_sector_classification() above for the working
# static-CSV replacement. Left in place in case NSE's protection changes.
# ==========================================
def fetch_nse_indices_details(create_json_files=True, input_data=None):
    """
    Fetches detailed components for each index concurrently.
    """
    OUTPUT_DIR = os.path.join(settings.DATA_DIR, 'indices')
    MAX_WORKERS = 4 

    if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)

    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://www.nseindia.com/market-data/live-equity-market',
        'Connection': 'keep-alive',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'X-Requested-With': 'XMLHttpRequest'
    }

    URL_HOME = "https://www.nseindia.com"
    URL_DETAILS_API = "https://www.nseindia.com/api/equity-stockIndices"

    session = requests.Session()
    session.headers.update(HEADERS)

    def init_session():
        try:
            time.sleep(1) 
            response = session.get(URL_HOME, timeout=15)
            if response.status_code == 200:
                return True
        except Exception:
            pass
        return False

    def fetch_and_save_index(index_name):
        url = f"{URL_DETAILS_API}?index={index_name.replace(' ', '%20').replace('&', '%26')}"
        try:
            time.sleep(random.uniform(0.5, 1.5))
            response = session.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                return {index_name: data}
            else:
                log_message(f"⚠️ Failed {index_name}: {response.status_code}")
        except Exception as e:
            log_message(f"❌ Error {index_name}: {e}")
        return None

    if not init_session():
        log_message("❌ CRITICAL: Could not initialize NSE Session. Aborting.")
        return {}

    indices_list = []
    if input_data:
        indices_list = input_data
        log_message(f"🔹 Using {len(indices_list)} indices from memory.")
    else:
        indices_file = os.path.join(settings.DATA_DIR, 'NSE_Indices_List.json')
        if os.path.exists(indices_file):
            with open(indices_file, 'r') as f:
                indices_list = json.load(f)
        else:
             log_message("❌ Indices list not found. Run Cell 4 first.")
             return {}
             
    target_indices = [item['index'] for item in indices_list if item.get('index')]
    log_message(f"🚀 Starting Concurrent Fetch for {len(target_indices)} indices...")
    
    results_data = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_index = {executor.submit(fetch_and_save_index, idx): idx for idx in target_indices}
        for future in tqdm(concurrent.futures.as_completed(future_to_index), total=len(target_indices), desc="Fetching Indices"):
            result = future.result()
            if result:
                results_data.update(result)
    
    if create_json_files:
        for index_name, data in results_data.items():
            safe_name = index_name.replace(' ', '_').replace(':', '_')
            file_path = os.path.join(OUTPUT_DIR, f"{safe_name}.json")
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
        log_message(f"✅ Saved {len(results_data)} index files to {OUTPUT_DIR}")
    else:
        log_message(f"✅ Fetched {len(results_data)} indices (In-Memory)")

    return results_data
