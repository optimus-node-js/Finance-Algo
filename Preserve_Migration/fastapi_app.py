import os
import time
import datetime
import pandas as pd
import numpy as np
import pyotp
import requests
import json
import io
import random
import concurrent.futures
import threading
import re
import uuid
import logging
from collections import defaultdict
from sqlalchemy import create_engine, text, MetaData, Table, Column, Integer, String, Float, Boolean, JSON, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import insert
from SmartApi import SmartConnect
# Changing tqdm.notebook to tqdm for script compatibility
from tqdm import tqdm 
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel

# --- GLOBAL LOGGING SETUP ---
LOG_DIR = 'logs'
if not os.path.exists(LOG_DIR): os.makedirs(LOG_DIR)
GLOBAL_LOG_FILE = os.path.join(LOG_DIR, 'server_operations.log')

def log_message(msg, console=True):
    """
    Logs a message to the global log file.
    Optionally prints to console (default: True).
    """
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    formatted_msg = f"[{timestamp}] {msg}"
    
    # 1. Write to File
    try:
        with open(GLOBAL_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(formatted_msg + "\n")
    except Exception as e:
        print(f"❌ Logging Error: {e}")

    # 2. Print to Console
    if console:
        print(formatted_msg)

# Setup Logging (Standard Lib for other libraries if needed)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger()

# --- GLOBAL STATE ---
engines = {"local": None, "neon": None}
key_manager = None

# --- CELL 1: Database Connection ---
def run_cell_1():
    global engines
    # Cell 1: Database Connection
    
    # --- 1. LOCAL CONFIGURATION ---
    DB_USER = "postgres"
    DB_PASS = "postgres"
    DB_HOST = "localhost"
    DB_PORT = "5432"
    DB_NAME = "postgres"

    LOCAL_DB_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    
    # --- 2. NEON CONFIGURATION ---
    NEON_DB_URL = "postgresql://neondb_owner:***REDACTED***@ep-late-feather-a127cgma-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

    # Connect Local
    try:
        local_engine = create_engine(LOCAL_DB_URL)
        with local_engine.connect() as conn:
            res = conn.execute(text("SELECT 1")).scalar()
            log_message(f"✅ [LOCAL] Database Connection Successful! (Test result: {res})")
        engines["local"] = local_engine
    except Exception as e:
        log_message(f"❌ [LOCAL] Connection Failed: {e}")

    # Connect Neon
    try:
        neon_engine = create_engine(NEON_DB_URL)
        with neon_engine.connect() as conn:
            res = conn.execute(text("SELECT 1")).scalar()
            log_message(f"✅ [NEON] Database Connection Successful! (Test result: {res})")
        engines["neon"] = neon_engine
    except Exception as e:
        log_message(f"❌ [NEON] Connection Failed: {e}")
       
    return engines

# --- CELL 2: Angel Session ---
def run_cell_2():
    global key_manager
    
    # ==========================================
    # 1. MULTI-KEY CONFIGURATION
    # ==========================================
    # Add your 5 API Keys here. 
    # NOTE: The CLIENT_CODE and PASSWORD usually remain the same, 
    # but if you have 5 different accounts, change those too.
    CREDENTIALS_POOL = [
        {"api_key": "***REDACTED***", "client": "***REDACTED***", "pwd": "1234", "totp": "***REDACTED***"},
        {"api_key": "***REDACTED***", "client": "***REDACTED***", "pwd": "1234", "totp": "***REDACTED***"},
        {"api_key": "***REDACTED***", "client": "***REDACTED***", "pwd": "1234", "totp": "***REDACTED***"},
        {"api_key": "***REDACTED***", "client": "***REDACTED***", "pwd": "1234", "totp": "***REDACTED***"},
        {"api_key": "***REDACTED***", "client": "***REDACTED***", "pwd": "1234", "totp": "***REDACTED***"},
    ]

    class AngelKeyManager:
        def __init__(self, creds_list):
            self.sessions = []
            self.current_index = 0
            log_message(f"Initializing {len(creds_list)} API Sessions...")
            
            for cred in creds_list:
                try:
                    obj = SmartConnect(api_key=cred['api_key'])
                    totp = pyotp.TOTP(cred['totp']).now()
                    data = obj.generateSession(cred['client'], cred['pwd'], totp)
                    if data['status']:
                        self.sessions.append(obj)
                        log_message(f" >> Session Active for Key: ...{cred['api_key'][-4:]}")
                    else:
                        log_message(f" >> Failed for Key: ...{cred['api_key'][-4:]} | {data['message']}")
                except Exception as e:
                    log_message(f" >> Error: {e}")
            
            if not self.sessions:
                raise Exception("No API Sessions could be established!")

        def get_session(self):
            """Returns a session and rotates the index"""
            session = self.sessions[self.current_index]
            # Rotate to next key
            self.current_index = (self.current_index + 1) % len(self.sessions)
            return session

    # Initialize the Pool (Run this once)
    key_manager = AngelKeyManager(CREDENTIALS_POOL)
    log_message("Setup Complete. Uncomment the initialization line above after filling credentials.")

# --- CELL 3: Fetch NSE Official CSV & Initialize Master Skeleton ---
def run_cell_3():
    # CELL 3: Fetch NSE Official CSV & Initialize Master Skeleton (Strict Raw Mode)
    
    # ==========================================
    # 1. CONFIGURATION
    # ==========================================
    # Official NSE Equity List
    NSE_CSV_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

    DATA_DIR = 'data'
    OUTPUT_FILE = os.path.join(DATA_DIR, 'NSE_STOCKS_SKELETON.json')

    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Connection': 'keep-alive'
    }

    # ==========================================
    # 2. FETCH AND PARSE CSV (STRICT)
    # ==========================================
    def fetch_nse_csv():
        log_message(f"📥 Downloading NSE Equity CSV...")
        log_message(f"🌐 URL: {NSE_CSV_URL}")
        try:
            response = requests.get(NSE_CSV_URL, headers=HEADERS, timeout=15)
            
            if response.status_code == 200:
                raw_text = response.content.decode('utf-8')
                
                # Debug: Count exact lines from server
                total_lines = len(raw_text.splitlines())
                log_message(f"🔹 Raw content received from Server: {total_lines} lines")
                
                # Read CSV content
                # keep_default_na=False prevents empty strings becoming NaN (we handle it manually)
                csv_content = io.StringIO(raw_text)
                df = pd.read_csv(csv_content, keep_default_na=False)
                
                # Clean column names (strip spaces)
                df.columns = [c.strip() for c in df.columns]
                
                log_message(f"✅ Pandas Parsed Rows: {len(df)}")
                
                # Mismatch Check
                # (Total Lines - 1 Header) should equal df rows. 
                # If Raw is 2227, DataFrame should be 2226.
                if len(df) != (total_lines - 1):
                    log_message(f"⚠️ Warning: Potential Row Mismatch! Raw: {total_lines-1} vs Parsed: {len(df)}")
                    log_message("   (This might be due to empty trailing lines in the CSV file)")
                
                return df
            else:
                log_message(f"❌ Failed to download. Status Code: {response.status_code}")
                return None
                
        except Exception as e:
            log_message(f"❌ Exception during download: {e}")
            return None

    # ==========================================
    # 3. BUILD MASTER JSON OBJECTS
    # ==========================================
    def build_master_skeleton(df):
        master_list = []
        
        log_message(f"🏗️ Building Master JSON Structure...")
        
        for _, row in df.iterrows():
            # Clean basic fields
            symbol = str(row.get('SYMBOL', '')).strip()
            series = str(row.get('SERIES', '')).strip()
            name = str(row.get('NAME OF COMPANY', '')).strip()
            isin = str(row.get('ISIN NUMBER', '')).strip()
            listing_date = str(row.get('DATE OF LISTING', '')).strip()
            
            # Handle Numeric conversions safely
            try:
                mkt_lot = int(row.get('MARKET LOT', 1))
            except:
                mkt_lot = 1
                
            # --- THE MASTER OBJECT ---
            stock_obj = {
                "master_key": symbol,
                
                # 1. Normalized Fields
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
                
                # 2. RAW DATA STORAGE (Store Everything)
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

    # ==========================================
    # 4. EXECUTION
    # ==========================================
    def main():
        df = fetch_nse_csv()
        
        if df is not None:
            data = build_master_skeleton(df)
            
            # Save to JSON
            with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
                
            log_message(f"\\n" + "="*50)
            log_message(f"🎉 SKELETON CREATED: {OUTPUT_FILE}")
            log_message(f"🔹 Total Items in File : {len(data)}")
            log_message("="*50)

    main()

# --- CELL 4: NSE Indices List Fetcher ---
def run_cell_4():
    # CELL 4: NSE Indices List Fetcher (JSON + Postgres + Schema Reset)
    # DEPENDENCY: Requires Cell 1 (Database Connection) to be run first.

    # ==========================================
    # 1. CONFIGURATION & TOGGLES
    # ==========================================
    STORE_IN_JSON = True       
    STORE_IN_POSTGRES = True   

    # File Config
    DATA_DIR = 'data'
    LOG_DIR = 'logs'
    OUTPUT_FILE = os.path.join(DATA_DIR, 'NSE_Indices_List.json')

    if not os.path.exists(DATA_DIR): os.makedirs(DATA_DIR)
    if not os.path.exists(LOG_DIR): os.makedirs(LOG_DIR)

    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate', 
        'Referer': 'https://www.nseindia.com/market-data/live-market-indices',
        'Connection': 'keep-alive',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'X-Requested-With': 'XMLHttpRequest'
    }

    URL_HOME = "https://www.nseindia.com"
    URL_API = "https://www.nseindia.com/api/allIndices"

    # ==========================================
    # 2. HELPER FUNCTIONS
    # ==========================================
    
    def define_indices_schema(metadata):
        """Defines the indices table schema."""
        return Table(
            'indices', metadata,
            Column('id', Integer, primary_key=True, autoincrement=True),
            Column('name', String, unique=True, nullable=False),
            Column('key', String),
            Column('index_symbol', String),
            Column('is_fno_eligible', Boolean),
            Column('last_price', Float),
            Column('percent_change', Float),
            Column('previous_close', Float),
            Column('open_price', Float),
            Column('high_price', Float),
            Column('low_price', Float),
            Column('year_high', Float),
            Column('year_low', Float),
            Column('pe_ratio', String),
            Column('pb_ratio', String),
            Column('div_yield', String),
            Column('raw_meta', String)
        )

    def ensure_table_exists():
        """Creates the table if it doesn't exist (Empty) on ALL active engines."""
        metadata = MetaData()
        define_indices_schema(metadata)
        
        for name, engine in engines.items():
            if engine is None: continue
            try:
                metadata.create_all(engine)
                log_message(f"⚠️ [{name.upper()}] Ensured 'indices' table exists.")
            except Exception as e:
                log_message(f"❌ [{name.upper()}] Failed to ensure table existence: {e}")

    def save_to_postgres(data_list):
        """Saves indices to ALL connected Postgres databases"""
        success_count = 0
        
        for name, engine in engines.items():
            if engine is None:
                log_message(f"⚠️ [{name.upper()}] Engine not available. Skipping.")
                continue

            try:
                # --- FIX: DROP TABLE TO ENSURE FRESH SCHEMA ---
                with engine.begin() as conn:
                    conn.execute(text("DROP TABLE IF EXISTS indices CASCADE"))
                
                metadata = MetaData()
                indices_table = define_indices_schema(metadata)
                
                # Create table newly
                metadata.create_all(engine)
                
                # --- PREPARE DATA BATCH ---
                records_batch = []
                for item in data_list:
                    record = {
                        "name": item.get("index", "").strip(),
                        "key": item.get("key", ""),
                        "index_symbol": item.get("indexSymbol", ""),
                        "is_fno_eligible": item.get("isDerivativesEligible", False),
                        "last_price": item.get("last"),
                        "percent_change": item.get("percentChange"),
                        "previous_close": item.get("previousClose"),
                        "open_price": item.get("open"),
                        "high_price": item.get("high"),
                        "low_price": item.get("low"),
                        "year_high": item.get("yearHigh"),
                        "year_low": item.get("yearLow"),
                        "pe_ratio": str(item.get("Price-to-Earnings-ratio", "")),
                        "pb_ratio": str(item.get("Price-to-Book-ratio", "")),
                        "div_yield": str(item.get("Dividend-Yield", "")),
                        "raw_meta": json.dumps(item)
                    }
                    records_batch.append(record)

                log_message(f"🗄️  [{name.upper()}] Schema Reset. Starting Bulk Upsert...")
                
                with engine.begin() as conn:
                    if records_batch:
                        # Upsert Logic
                        stmt = insert(indices_table).values(records_batch)
                        update_stmt = stmt.on_conflict_do_update(
                            index_elements=['name'],
                            set_={
                                "key": stmt.excluded.key,
                                "last_price": stmt.excluded.last_price,
                                "percent_change": stmt.excluded.percent_change,
                                "raw_meta": stmt.excluded.raw_meta,
                                "is_fno_eligible": stmt.excluded.is_fno_eligible
                            }
                        )
                        conn.execute(update_stmt)
                        
                log_message(f"✅ [{name.upper()}] Update Complete: {len(data_list)} records.")
                success_count += 1
                
            except Exception as e:
                log_message(f"❌ [{name.upper()}] Database Error: {str(e)}")
        
        return success_count > 0

    # ==========================================
    # 3. MAIN LOGIC
    # ==========================================
    def fetch_indices_manual():
        log_message("🚀 Starting Fetch via Manual Session...")
        session = requests.Session()
        session.headers.update(HEADERS)
        
        # RETRY LOGIC
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    log_message(f"🔄 Retry {attempt+1}/{max_retries}...")
                    time.sleep(random.uniform(2, 5))

                log_message(f"🌐 Fetching Home: {URL_HOME}")
                response_home = session.get(URL_HOME, timeout=15)
                if response_home.status_code != 200: 
                    continue
                
                time.sleep(random.uniform(2, 4))
                
                log_message(f"🌐 Fetching Indices API: {URL_API}")
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
            except Exception as e:
                log_message(f"❌ Exception (Attempt {attempt+1}): {str(e)}")
        
        return None

    def main():
        data = fetch_indices_manual()
        if data:
            if STORE_IN_JSON:
                with open(OUTPUT_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)
                log_message(f"✅ Saved JSON: {OUTPUT_FILE}")
            if STORE_IN_POSTGRES:
                save_to_postgres(data)
        else:
            log_message("❌ FAILURE: Could not fetch data.")
            # FALLBACK: Ensure table exists so downstream cells don't crash
            if STORE_IN_POSTGRES:
                ensure_table_exists()

    main()

# --- CELL 5: High-Speed Concurrent NSE Index Fetcher ---
def run_cell_5():
    # CELL 5: High-Speed Concurrent NSE Index Fetcher
    
    # ==========================================
    # 1. CONFIGURATION
    # ==========================================
    OUTPUT_DIR = 'data/indices'
    # LOG_FILE = 'logs/fast_fetch.log' # Using global logger

    # CONCURRENCY SETTINGS
    # ⚠️ WARNING: Higher workers = Faster speed, but HIGHER risk of IP Ban.
    # Recommended: 3 to 5. Do not go above 10.
    MAX_WORKERS = 4 

    if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)
    if not os.path.exists('logs'): os.makedirs('logs')

    # Headers (Mimicking Chrome)
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate',
        'Referer': 'https://www.nseindia.com/market-data/live-equity-market',
        'Connection': 'keep-alive',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'X-Requested-With': 'XMLHttpRequest'
    }

    URL_HOME = "https://www.nseindia.com"
    URL_MASTER_LIST = "https://www.nseindia.com/api/allIndices"
    URL_DETAILS_API = "https://www.nseindia.com/api/equity-stockIndices"

    # ==========================================
    # 2. GLOBAL SESSION MANAGEMENT
    # ==========================================
    # We use a global session to reuse the TCP connection (Keep-Alive)
    session = requests.Session()
    session.headers.update(HEADERS)

    def init_session():
        """Hits homepage to get cookies"""
        try:
            # random sleep before init to be safe
            time.sleep(1) 
            log_message(f"🌐 Initializing Session: {URL_HOME}")
            response = session.get(URL_HOME, timeout=15)
            if response.status_code == 200:
                return True
        except Exception:
            pass
        return False

    # ==========================================
    # 3. WORKER FUNCTION (The "Engine")
    # ==========================================
    def fetch_and_save_index(index_name):
        """
        This function runs inside each thread.
        1. Fetches data for one index.
        2. Cleans charts.
        3. Saves to JSON.
        """
        safe_filename = index_name.replace(" ", "_").replace("/", "-")
        file_path = os.path.join(OUTPUT_DIR, f"{safe_filename}.json")
        
        params = {"index": index_name}
        
        try:
            # Random tiny sleep to jitter the threads so they don't hit exact same millisecond
            time.sleep(random.uniform(0.5, 1.5))
            
            # log_message(f"🌐 Fetching Index Details: {index_name} from {URL_DETAILS_API}") # Too verbose?
            response = session.get(URL_DETAILS_API, params=params, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                
                # --- CLEANING LOGIC ---
                # 1. Remove top-level charts if they exist
                keys_to_remove = ["chartTodayPath", "chart30dPath", "chart365dPath"]
                for k in keys_to_remove:
                    data.pop(k, None)
                    
                # 2. Remove charts from the specific "data" list items if they exist there
                if "data" in data and isinstance(data["data"], list):
                    for stock in data["data"]:
                        if isinstance(stock, dict):
                            for k in keys_to_remove:
                                stock.pop(k, None)

                # --- SAVING ---
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4)
                    
                return {"status": "success", "index": index_name}
                
            elif response.status_code == 401:
                return {"status": "retry", "index": index_name}
            else:
                return {"status": "error", "index": index_name, "code": response.status_code}
                
        except Exception as e:
            return {"status": "error", "index": index_name, "msg": str(e)}

    # ==========================================
    # 4. MAIN ORCHESTRATOR
    # ==========================================
    def main():
        log_message(f"🚀 Starting High-Speed Concurrent Fetcher...")
        
        # --- Phase 1: Get Master List (In-Memory) ---
        log_message(f"🍪 Initializing Session & Fetching Master List...")
        if not init_session():
            log_message("❌ Failed to initialize session. Check internet/IP.")
            return

        try:
            log_message(f"🌐 Fetching Master List from: {URL_MASTER_LIST}")
            res = session.get(URL_MASTER_LIST, timeout=15)
            raw_data = res.json()
            
            # Unwrap data if inside "data" key
            if isinstance(raw_data, dict) and "data" in raw_data:
                master_list = raw_data["data"]
            else:
                master_list = raw_data
                
            # Extract just the Index Names (Symbols)
            # logic: prefer 'indexSymbol', fallback to 'index', then 'key'
            index_names = []
            for item in master_list:
                name = item.get("indexSymbol") or item.get("index") or item.get("key")
                if name:
                    index_names.append(name)
            
            # Unique names only
            index_names = list(set(index_names))
            log_message(f"✅ Master List Fetched. Found {len(index_names)} indices.")
            
        except Exception as e:
            log_message(f"❌ Failed to fetch Master List: {e}")
            return

        # --- Phase 2: Concurrent Fetching ---
        log_message(f"⚡ Starting Parallel Fetch (Workers: {MAX_WORKERS})...")
        
        results_log = {"success": 0, "retry": 0, "error": 0}
        
        # ThreadPoolExecutor manages the pool of worker threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Submit all tasks
            future_to_index = {executor.submit(fetch_and_save_index, name): name for name in index_names}
            
            # Process as they complete (using tqdm for progress bar)
            for future in tqdm(concurrent.futures.as_completed(future_to_index), total=len(index_names), desc="Fetching"):
                result = future.result()
                status = result["status"]
                
                if status == "success":
                    results_log["success"] += 1
                elif status == "retry":
                    # If 401, we might need to re-queue or just log (Simple version: Log it)
                    # Implementing re-queue in concurrency is complex, usually just re-run script for missed ones
                    results_log["retry"] += 1
                else:
                    results_log["error"] += 1

        log_message("\\n" + "="*40)
        log_message(f"🏁 FINAL REPORT")
        log_message(f"✅ Success: {results_log['success']}")
        log_message(f"⚠️ Failed/Auth Error: {results_log['retry']}")
        log_message(f"❌ Errors: {results_log['error']}")
        log_message(f"📂 Data saved to: {OUTPUT_DIR}/")
        log_message("="*40)

    main()

# --- CELL 6: The Local Enricher ---
def run_cell_6():
    # CELL 6: The Local Enricher (In-Place Update of Master JSON)
    
    # ==========================================
    # 1. CONFIGURATION
    # ==========================================
    # We read AND write to the same file to keep it as the Single Source of Truth
    MASTER_FILE = 'data/NSE_STOCKS_SKELETON.json'
    INDICES_DIR = 'data/indices'

    # ==========================================
    # 2. BUILD LOOKUP MAP (The "Reverse Index")
    # ==========================================
    def build_indices_lookup():
        log_message(f"📖 Scanning Index files to build Lookup Map...")
        
        # Map: Symbol -> { "meta": ..., "indices": [list of index names], "web_data": ... }
        lookup_map = defaultdict(lambda: {"meta": None, "indices": []})
        
        if not os.path.exists(INDICES_DIR):
            log_message(f"❌ Error: {INDICES_DIR} directory not found.")
            return {}

        files = [f for f in os.listdir(INDICES_DIR) if f.endswith('.json')]
        
        for filename in tqdm(files, desc="Scanning Indices"):
            index_name = filename.replace('.json', '').replace('_', ' ')
            file_path = os.path.join(INDICES_DIR, filename)
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = json.load(f)
                    
                # Handle Wrapper
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
                    
                    # Link Stock to Index
                    lookup_map[symbol]["indices"].append(index_name)
                    
                    # Capture the richest metadata
                    # We prioritize metadata that has "industry" info
                    new_meta = stock.get("meta")
                    current_meta = lookup_map[symbol]["meta"]
                    
                    if new_meta:
                        if current_meta is None:
                            # First time seeing meta, grab it
                            lookup_map[symbol]["web_data"] = stock
                            lookup_map[symbol]["meta"] = new_meta
                        elif new_meta.get("industry") and not current_meta.get("industry"):
                            # Found better meta with industry, upgrade
                            lookup_map[symbol]["web_data"] = stock
                            lookup_map[symbol]["meta"] = new_meta

            except Exception as e:
                log_message(f"❌ Error reading {filename}: {e}")
                
        return lookup_map

    # ==========================================
    # 3. UPDATE MASTER JSON IN-PLACE
    # ==========================================
    def update_master_file(lookup_map):
        if not os.path.exists(MASTER_FILE):
            log_message(f"❌ Error: {MASTER_FILE} not found. Run Cell 3 first.")
            return

        log_message(f"🔄 Loading Master JSON...")
        with open(MASTER_FILE, 'r', encoding='utf-8') as f:
            master_list = json.load(f)

        log_message(f"⚡ Enriching fields...")
        enriched_count = 0
        
        for stock_obj in tqdm(master_list, desc="Updating Stocks"):
            symbol = stock_obj["master_key"]
            
            # Check if we found this stock in our Indices scan
            if symbol in lookup_map:
                match = lookup_map[symbol]
                web_data = match.get("web_data")
                indices_list = match.get("indices")
                meta = match.get("meta", {})
                
                # --- 1. FILL RAW SOURCE (Source C) ---
                stock_obj["__RAW_DATA_SOURCES__"]["source_nse_web"] = web_data
                
                # --- 2. UPDATE CLASSIFICATION ---
                if meta.get("industry"):
                    stock_obj["classification"]["industry"] = meta.get("industry")
                
                # --- 3. UPDATE IDENTIFIERS ---
                if meta.get("slb_isin"):
                    stock_obj["identifiers"]["slb_isin"] = meta.get("slb_isin")
                
                if meta.get("activeSeries"):
                    stock_obj["identifiers"]["active_series"] = meta.get("activeSeries")

                # --- 4. UPDATE FLAGS ---
                # We use .get(key, False) to ensure it defaults to False if missing
                stock_obj["flags"]["is_fno"] = meta.get("isFNOSec", False)
                stock_obj["flags"]["is_etf"] = meta.get("isETFSec", False)
                stock_obj["flags"]["is_slb_eligible"] = meta.get("isSLBSec", False)
                stock_obj["flags"]["is_suspended"] = meta.get("isSuspended", False)
                stock_obj["flags"]["is_delisted"] = meta.get("isDelisted", False)
                stock_obj["flags"]["is_municipal_bond"] = meta.get("isMunicipalBond", False)
                stock_obj["flags"]["is_mtf_eligible"] = False # Placeholder, Angel API will fill this later
                
                # --- 5. ADD RELATIONS (New Field) ---
                # This helps us query "Get all Nifty 50 stocks" later
                stock_obj["relations"] = {
                    "indices": indices_list
                }
                
                enriched_count += 1
            else:
                # If stock not in any index, just add empty relations for consistency
                stock_obj["relations"] = {"indices": []}

        # --- SAVE BACK TO SAME FILE ---
        log_message(f"💾 Saving updates to {MASTER_FILE}...")
        with open(MASTER_FILE, 'w', encoding='utf-8') as f:
            json.dump(master_list, f, indent=4)

        log_message(f"\\n" + "="*50)
        log_message(f"✅ UPDATE COMPLETE")
        log_message(f"🔹 File Updated: {MASTER_FILE}")
        log_message(f"🔹 Stocks Enriched: {enriched_count}/{len(master_list)}")
        log_message("="*50)

    # ==========================================
    # 4. EXECUTION
    # ==========================================
    lookup_map = build_indices_lookup()
    if lookup_map:
        update_master_file(lookup_map)

# --- CELL 7: The Token Hunter ---
def run_cell_7():
    # CELL 7: The Token Hunter (Final Fixed Headers & Logic)
    
    # ==========================================
    # 1. CONFIGURATION
    # ==========================================
    JSON_PATH = 'data/NSE_STOCKS_SKELETON.json'
    LOG_DIR = 'logs'
    SEARCH_URL = "https://global-search-bbe-prod.angelone.in/v5/search"
    MAX_WORKERS = 10 

    if not os.path.exists(LOG_DIR): os.makedirs(LOG_DIR)

    MISSING_LOG_PATH = os.path.join(LOG_DIR, 'angel_missing_stocks.log')

    # ---------------------------------------------------------
    # 🚨 PASTE YOUR FRESH COOKIE HERE FROM BROWSER NETWORK TAB 🚨
    # ---------------------------------------------------------
    COOKIE_STRING = "_gcl_au=1.1.1519322142.1762928355; WZRK_G=226ec77c75cf44c6974c1b30f06783bd; afUserId=63798698-4137-4c36-9e45-6c21eaf5a2f4-p; ABUserCookie=eJyrVgotTi3yS8xNVbJSCnL0CPVRUAjzDPYId/R2DPJ1VNJRcs7JTM0rcc5PAaswNbAwMre0NAFKJBXlZ6cWAQUd/dxdfcACiXnJGSCBEGePUCegiLuvZ4qSlaFhLQAJIBzV; AF_SYNC=1763805445664; _gid=GA1.2.461518422.1763977413; prod_non_trade_access_token=***REDACTED***; _ga=GA1.2.576070644.1762928356; _uetsid=0aadbf30c91a11f0ae014d065bfc7e59; _uetvid=8526dfc0bf8f11f09fab033a76f3874e; prod_trade_access_token=***REDACTED***; ShowMpin=1764061334798; _ga_CDX93S7LDP=GS2.1.s1764017944$o7$g0$t1764018134$j60$l0$h0" 

    # ==========================================
    # 2. EXACT HEADERS FROM YOUR SCREENSHOTS
    # ==========================================
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
        # FROM YOUR SCREENSHOT
        "X-Deviceid": "cb063c0f-d478-5a2d-b803-4beaa4bc7166", 
        "X-Clientpublicip": "2409:40c4:4b:22b5:1c92:1114:bc1b:4e59", # Added this crucial header
        "Cookie": COOKIE_STRING
    }

    # Thread-local storage for persistent sessions
    thread_local = threading.local()

    def get_session():
        if not hasattr(thread_local, "session"):
            thread_local.session = requests.Session()
            thread_local.session.headers.update(HEADERS_TEMPLATE)
        return thread_local.session

    # ==========================================
    # 3. HELPER FUNCTIONS
    # ==========================================
    def log_missing(company_name, tried_queries, reason):
        with open(MISSING_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(f"MISSING: {company_name} | Queries: {tried_queries} | Reason: {reason}\\n")

    def clean_company_name(name):
        """Removes suffixes like 'Limited', '(India) Ltd'."""
        cleaned = re.sub(r'\\s+\\(?(Limited|Ltd|India|LTD|LIMITED|INDIA|Plc|Corp|Corporation)\\)?\\.?$', '', name, flags=re.IGNORECASE)
        cleaned = re.sub(r'\\s+\\([^)]*\\)', '', cleaned)
        return cleaned.strip()

    def perform_search(keyword):
        """Hits Angel API."""
        session = get_session()
        # Check if user updated the cookie
        if "YOUR_HUGE_COOKIE" in session.headers['Cookie']:
            log_message("❌ ERROR: You forgot to paste the fresh Cookie in CONFIGURATION!")
            raise ValueError("Invalid Cookie")

        headers = session.headers.copy()
        headers["X-Requestid"] = str(uuid.uuid4())
        
        # Exact payload matching your browser
        params = {
            "userType": "B2C",
            "accountType": "D",
            "keyword": keyword
        }
        
        try:
            # Log to file ONLY to keep console clean for progress bar
            log_message(f"🔎 Searching Angel API: {SEARCH_URL} | Keyword: {keyword}", console=False)
            response = session.get(SEARCH_URL, headers=headers, params=params, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success" and "data" in data and "dataList" in data["data"]:
                    return data["data"]["dataList"]
        except Exception:
            pass
        return []

    def parse_best_match(results, search_symbol=None):
        """Finds the best NSE Equity match."""
        if not results: return None
        
        # 1. Exact Symbol Match
        if search_symbol:
            for item in results:
                if item.get("type") == "scrip" and item.get("symbolName") == search_symbol:
                    return item

        # 2. Strict NSE EQ
        for item in results:
            if item.get("type") == "scrip":
                if item.get("scripType") == "nseCash" and item.get("series") == "EQ":
                    return item
                # Backup for edge cases
                if item.get("scripType") == "nseCash" and item.get("tradeSymbol", "").endswith("-EQ"):
                    return item
        return None

    def fetch_and_enrich_stock(stock_obj):
        # Skip if already done
        if stock_obj["trading_info"].get("token_nse") and stock_obj["__RAW_DATA_SOURCES__"].get("source_angel_api"):
            return stock_obj

        identifiers = stock_obj.get("identifiers", {})
        symbol = identifiers.get("symbol")
        company_name = identifiers.get("company_name", "").strip()
        
        if not symbol or not company_name: return stock_obj

        match = None
        tried_queries = []

        # --- 1. Search by Symbol ---
        search_query = symbol
        tried_queries.append(search_query)
        results = perform_search(search_query)
        match = parse_best_match(results, search_symbol=symbol)

        # --- 2. Search by FULL Company Name (Browser Style) ---
        if not match:
            search_query = company_name
            tried_queries.append(search_query)
            results = perform_search(search_query)
            match = parse_best_match(results, search_symbol=symbol)

        # --- 3. Search by Clean Name ---
        if not match:
            cleaned_name = clean_company_name(company_name)
            if cleaned_name != company_name:
                search_query = cleaned_name
                tried_queries.append(search_query)
                results = perform_search(search_query)
                match = parse_best_match(results, search_symbol=symbol)

        # --- UPDATE ---
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

        return stock_obj

    # ==========================================
    # 4. EXECUTION
    # ==========================================
    def main():
        if not os.path.exists(JSON_PATH):
            log_message(f"❌ Error: {JSON_PATH} not found.")
            return

        # Clear logs
        open(MISSING_LOG_PATH, 'w').close()

        log_message(f"🔄 Loading Master JSON...")
        with open(JSON_PATH, 'r', encoding='utf-8') as f:
            master_list = json.load(f)

        pending = [s for s in master_list if not s["trading_info"].get("token_nse")]
        log_message(f"🕵️ Sweeping {len(pending)} missing stocks...")
        
        if len(pending) == 0:
            log_message("✅ No missing stocks to process!")
            return

        updated_list = []
        # Using 5 workers to be gentle with the specific headers
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            updated_list = list(tqdm(executor.map(fetch_and_enrich_stock, master_list), total=len(master_list)))

        success_count = sum(1 for s in updated_list if s["trading_info"].get("token_nse"))

        log_message(f"💾 Saving...")
        with open(JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(updated_list, f, indent=4)

        log_message(f"\\n" + "="*50)
        log_message(f"✅ COMPLETE")
        log_message(f"🔹 Total Tokens Found: {success_count}")
        log_message(f"🔹 Check logs at: {MISSING_LOG_PATH}")
        log_message("="*50)

    main()

# --- CELL 8: Stocks Master Seeder ---
def run_cell_8():
    # CELL 8: Stocks Master Seeder (JSON -> Postgres)
    # DEPENDENCY: Requires Cell 1 (Engine) and Cell 4 (Indices Table Populated)
    
    # ==========================================
    # 1. CONFIGURATION
    # ==========================================
    JSON_SOURCE = 'data/NSE_STOCKS_SKELETON.json'

    # ==========================================
    # 2. SCHEMA DEFINITION
    # ==========================================
    def init_schema(engine):
        metadata = MetaData()
        
        # Reflect existing 'indices' table to allow Foreign Key linking
        try:
            indices_table = Table('indices', metadata, autoload_with=engine)
            log_message("✅ Detected existing 'indices' table.")
        except Exception as e:
            log_message(f"❌ CRITICAL ERROR: Table 'indices' not found. Run Cell 4 first!")
            raise e

        # Drop tables to reset schema (Since we are changing columns)
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS stock_indices_map CASCADE"))
            conn.execute(text("DROP TABLE IF EXISTS stocks_master CASCADE"))

        # --- TABLE: STOCKS_MASTER ---
        stocks_table = Table(
            'stocks_master', metadata,
            Column('id', Integer, primary_key=True, autoincrement=True),
            Column('master_key', String),
            Column('symbol', String, unique=True, nullable=False), # Unique Key
            Column('isin', String),
            Column('slb_isin', String),
            Column('company_name', String),
            
            # SERIES FLAGS (Replaces 'active_series' string)
            Column('is_eq', Boolean, default=False), # Equity
            Column('is_be', Boolean, default=False), # Book Entry
            Column('is_bl', Boolean, default=False), # Block Deal
            Column('is_bz', Boolean, default=False), # Trade to Trade
            
            # Trading Info
            Column('token_nse', String),
            Column('token_bse', String),
            Column('tick_size', Float),
            Column('lot_size', Integer),
            Column('listing_date', String),
            Column('currency', String),
            Column('segment', String),
            
            # Classification
            Column('industry', String),
            Column('sector', String),
            Column('market_cap_category', String),
            Column('market_cap_value', Float),
            
            # Other Flags
            Column('is_fno', Boolean),
            Column('is_etf', Boolean),
            Column('is_slb_eligible', Boolean),
            Column('is_mtf_eligible', Boolean),
            Column('is_suspended', Boolean),
            Column('is_delisted', Boolean),
            Column('is_debt', Boolean),
            Column('is_municipal_bond', Boolean),
            
            # Raw Data Vault
            Column('meta', JSON) 
        )
        
        # --- TABLE: STOCK_INDICES_MAP (Relations) ---
        stock_indices_map = Table(
            'stock_indices_map', metadata,
            Column('id', Integer, primary_key=True, autoincrement=True),
            Column('stock_symbol', String, ForeignKey('stocks_master.symbol', ondelete="CASCADE")),
            Column('index_id', Integer, ForeignKey('indices.id', ondelete="CASCADE")),
            UniqueConstraint('stock_symbol', 'index_id', name='uq_stock_index_pair')
        )
        
        metadata.create_all(engine)
        log_message("✅ Database Schema (stocks_master, stock_indices_map) Created.")
        return stocks_table, stock_indices_map

    # ==========================================
    # 3. SEEDER LOGIC
    # ==========================================
    def seed_database():
        if not os.path.exists(JSON_SOURCE):
            log_message(f"❌ Error: {JSON_SOURCE} not found.")
            return

        log_message(f"📖 Loading Master JSON...")
        with open(JSON_SOURCE, 'r', encoding='utf-8') as f:
            stock_list = json.load(f)

        for name, engine in engines.items():
            if engine is None:
                log_message(f"⚠️ [{name.upper()}] Engine not available. Skipping.")
                continue

            try:
                log_message(f"🚀 [{name.upper()}] Starting Bulk Upsert for {len(stock_list)} stocks...")
                
                # Initialize Schema for this engine
                stocks_table, relations_table = init_schema(engine)
                
                # Fetch Indices Map for this engine
                index_map = {}
                try:
                    with engine.connect() as conn:
                        # Check if indices table exists
                        if not engine.dialect.has_table(conn, "indices"):
                             log_message(f"⚠️ [{name.upper()}] 'indices' table missing. Skipping relations.")
                        else:
                            result = conn.execute(text("SELECT name, id FROM indices"))
                            for row in result:
                                index_map[row[0].upper()] = row[1]
                            log_message(f"🔗 [{name.upper()}] Found {len(index_map)} indices available for linking.")
                except Exception as e:
                     log_message(f"⚠️ [{name.upper()}] Failed to fetch indices map: {e}")

                # --- PREPARE DATA BATCHES ---
                stock_records_batch = []
                relation_records_batch = []

                for item in stock_list:
                    identifiers = item.get("identifiers", {})
                    trading = item.get("trading_info", {})
                    classif = item.get("classification", {})
                    flags = item.get("flags", {})
                    
                    symbol = identifiers.get("symbol")
                    if not symbol: continue
                    
                    # Handle Market Cap safely
                    mkt_val = classif.get("market_cap_value")
                    if mkt_val == "": mkt_val = None
                    
                    # Prepare Flags
                    is_eq = "EQ" in identifiers.get("active_series", [])
                    is_be = "BE" in identifiers.get("active_series", [])
                    is_bl = "BL" in identifiers.get("active_series", [])
                    is_bz = "BZ" in identifiers.get("active_series", [])
                    
                    tick = trading.get("tick_size")
                    if tick == "": tick = None

                    stock_record = {
                        "symbol": symbol,
                        "master_key": item.get("master_key"), # Ensure master_key is included
                        "company_name": identifiers.get("company_name"),
                        "isin": identifiers.get("isin"),
                        "slb_isin": identifiers.get("slb_isin"),
                        
                        "is_eq": is_eq,
                        "is_be": is_be,
                        "is_bl": is_bl,
                        "is_bz": is_bz,
                        
                        "token_nse": trading.get("token_nse"),
                        "token_bse": trading.get("token_bse"),
                        "tick_size": tick,
                        "lot_size": trading.get("lot_size"),
                        "listing_date": trading.get("listing_date"),
                        "currency": trading.get("currency", "INR"),
                        "segment": trading.get("segment", "EQUITY"),
                        
                        "industry": classif.get("industry"),
                        "sector": classif.get("sector"),
                        "market_cap_category": classif.get("market_cap_category"),
                        "market_cap_value": mkt_val,
                        
                        "is_fno": flags.get("is_fno", False),
                        "is_etf": flags.get("is_etf", False),
                        "is_slb_eligible": flags.get("is_slb_eligible", False),
                        "is_mtf_eligible": flags.get("is_mtf_eligible", False),
                        "is_suspended": flags.get("is_suspended", False),
                        "is_delisted": flags.get("is_delisted", False),
                        "is_debt": flags.get("is_debt", False),
                        "is_municipal_bond": flags.get("is_municipal_bond", False),
                        
                        "meta": item.get("__RAW_DATA_SOURCES__")
                    }
                    stock_records_batch.append(stock_record)
                    
                    # --- B. Relations ---
                    stock_indices = item.get("relations", {}).get("indices", [])
                                    
                    for index_name in stock_indices:
                        idx_id = index_map.get(index_name.upper())
                        if idx_id:
                            rel_record = {
                                "stock_symbol": item.get("master_key"),
                                "index_id": idx_id
                            }
                            relation_records_batch.append(rel_record)

                # --- EXECUTE BULK OPERATIONS ---
                with engine.begin() as conn:
                    # 1. Bulk Upsert Stocks
                    if stock_records_batch:
                        log_message(f"⚡ [{name.upper()}] Executing Batch Upsert for {len(stock_records_batch)} stocks...")
                        stmt = insert(stocks_table).values(stock_records_batch)
                        update_stmt = stmt.on_conflict_do_update(
                            index_elements=['symbol'],
                            set_={
                                "token_nse": stmt.excluded.token_nse,
                                "token_bse": stmt.excluded.token_bse,
                                "meta": stmt.excluded.meta,
                                "industry": stmt.excluded.industry,
                                "sector": stmt.excluded.sector,
                                "market_cap_value": stmt.excluded.market_cap_value,
                                "is_fno": stmt.excluded.is_fno,
                                "is_eq": stmt.excluded.is_eq,
                                "is_be": stmt.excluded.is_be,
                                "is_bl": stmt.excluded.is_bl,
                                "is_bz": stmt.excluded.is_bz
                            }
                        )
                        conn.execute(update_stmt)
                    
                    # 2. Bulk Insert Relations
                    if relation_records_batch:
                        log_message(f"⚡ [{name.upper()}] Executing Batch Insert for {len(relation_records_batch)} relations...")
                        rel_stmt = insert(relations_table).values(relation_records_batch)
                        rel_update = rel_stmt.on_conflict_do_nothing()
                        conn.execute(rel_update)

                log_message(f"✅ [{name.upper()}] DATABASE SEEDING COMPLETE!")
            except Exception as e:
                log_message(f"❌ [{name.upper()}] Database Error: {e}")

    seed_database()

# --- FASTAPI APP ---
app = FastAPI(title="Migration API", description="API for seeding stock data from notebook logic")

@app.on_event("startup")
def startup_event():
    # Initialize DB Connection on startup
    run_cell_1()

@app.get("/")
def read_root():
    return {"message": "Migration API is running. Use /docs to see endpoints."}

@app.post("/seed/indices")
def seed_indices_endpoint():
    """
    Runs Cell 4: Fetch Indices Manual & Update DB
    """
    try:
        run_cell_4()
        return {"status": "success", "message": "Indices seeded successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/seed/stocks/full-refresh")
def seed_stocks_full_refresh(background_tasks: BackgroundTasks):
    """
    Runs the full pipeline:
    1. Fetch NSE CSV (Cell 3)
    2. Fetch Indices (Cell 4)
    3. Fetch Index Details (Cell 5)
    4. Enrich with Indices (Cell 6)
    5. Enrich with Tokens (Cell 7)
    6. Seed DB (Cell 8)
    """
    def full_pipeline():
        run_cell_3()
        run_cell_4()
        run_cell_5()
        run_cell_6()
        run_cell_7()
        run_cell_8()
    
    background_tasks.add_task(full_pipeline)
    return {"status": "accepted", "message": "Full refresh started in background"}

@app.post("/seed/stocks/quick-update")
def seed_stocks_quick_update(background_tasks: BackgroundTasks):
    """
    Runs a quick update (skipping heavy token fetch if possible, or just DB seed)
    For now, let's just run Cell 8 (Seed DB from existing JSON)
    """
    def quick_pipeline():
        run_cell_8()

    background_tasks.add_task(quick_pipeline)
    return {"status": "accepted", "message": "Quick update (DB Seed) started in background"}

@app.delete("/cleanup")
def cleanup_endpoint():
    """
    Deletes:
    1. All logs in 'logs/'
    2. All JSONs in 'data/indices/'
    3. All JSONs in 'data/' EXCEPT 'OpenAPIScriptMaster.json'
    """
    deleted_files = []
    errors = []

    # 1. Clean Logs
    if os.path.exists(LOG_DIR):
        for f in os.listdir(LOG_DIR):
            file_path = os.path.join(LOG_DIR, f)
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    deleted_files.append(file_path)
            except Exception as e:
                errors.append(f"Failed to delete {file_path}: {e}")

    # 2. Clean Indices Data
    indices_dir = os.path.join('data', 'indices')
    if os.path.exists(indices_dir):
        for f in os.listdir(indices_dir):
            if f.endswith(".json"):
                file_path = os.path.join(indices_dir, f)
                try:
                    os.remove(file_path)
                    deleted_files.append(file_path)
                except Exception as e:
                    errors.append(f"Failed to delete {file_path}: {e}")

    # 3. Clean Data Root (Except OpenAPIScriptMaster)
    data_dir = 'data'
    if os.path.exists(data_dir):
        for f in os.listdir(data_dir):
            if f.endswith(".json") and "OpenAPIScriptMaster" not in f:
                file_path = os.path.join(data_dir, f)
                try:
                    os.remove(file_path)
                    deleted_files.append(file_path)
                except Exception as e:
                    errors.append(f"Failed to delete {file_path}: {e}")
    
    log_message(f"🧹 Cleanup Complete. Deleted {len(deleted_files)} files.")
    
    return {
        "status": "success", 
        "deleted_count": len(deleted_files), 
        "deleted_files": deleted_files,
        "errors": errors
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
