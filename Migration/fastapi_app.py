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
from sqlalchemy import create_engine, text, MetaData, Table, Column, Integer, String, Float, Boolean, JSON, ForeignKey, UniqueConstraint, inspect, BigInteger, DateTime, Text
from sqlalchemy.dialects.postgresql import insert
from SmartApi import SmartConnect
# Changing tqdm.notebook to tqdm for script compatibility
from tqdm import tqdm 
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# --- GLOBAL LOGGING SETUP ---
# --- GLOBAL LOGGING SETUP ---
if os.environ.get("VERCEL"):
    LOG_DIR = '/tmp/logs'
    DATA_DIR = '/tmp/data'
else:
    LOG_DIR = 'logs'
    DATA_DIR = 'data'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger()

# --- GLOBAL STATE ---
engines = {"local": None, "neon": None}
key_manager = None
GLOBAL_LOG_FILE = os.path.join(LOG_DIR, 'app.log')

# --- LOGGING FUNCTIONS ---
def log_message(msg, console=True):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"[{timestamp}] {msg}"
    
    # 1. Write to File
    try:
        if not os.path.exists(LOG_DIR): os.makedirs(LOG_DIR)
        with open(GLOBAL_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(formatted_msg + "\n")
    except Exception as e:
        print(f"❌ Logging Error: {e}")

    # 2. Print to Console
    if console:
        print(formatted_msg)

def init_logging_db(engine):
    """Creates the system_logs table if it doesn't exist."""
    metadata = MetaData()
    system_logs = Table(
        'system_logs', metadata,
        Column('id', BigInteger, primary_key=True, autoincrement=True),
        Column('timestamp', DateTime, default=datetime.datetime.utcnow),
        Column('level', String(20)),   # INFO, ERROR, WARNING
        Column('source', String(50)),  # angel_api, system, cron
        Column('event', String(50)),   # search, login, error
        Column('message', Text),
        Column('context', JSON)        # Extra data (url, params, etc)
    )
    metadata.create_all(engine)
    log_message("✅ Logging Table (system_logs) Verified.")

def log_to_db(level, source, event, message, context=None):
    """Writes a structured log entry to the DB (Neon preferred, fallback to Local)."""
    # Try Neon first, then Local
    engine = engines.get("neon") or engines.get("local")
    
    if not engine:
        # Fallback to file if no DB
        log_message(f"[{level}] {source}.{event}: {message} | {context}", console=True)
        return

    try:
        stmt = insert(Table('system_logs', MetaData(), autoload_with=engine)).values(
            level=level,
            source=source,
            event=event,
            message=message,
            context=context or {}
        )
        with engine.begin() as conn:
            conn.execute(stmt)
    except Exception as e:
        # Fallback to file if DB write fails
        log_message(f"⚠️ DB Log Failed: {e} | Original: {message}")

# --- CELL 1: Database Connection ---
def run_cell_1():
    global engines
    # Cell 1: Database Connection
    
    # --- 1. LOCAL CONFIGURATION ---
    DB_USER = "postgres"
    DB_PASS = "postgres"
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = "5432"
    DB_NAME = "postgres"

    LOCAL_DB_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    
    # --- 2. NEON CONFIGURATION ---
    NEON_DB_URL = "postgresql://neondb_owner:***REDACTED***@ep-late-feather-a127cgma-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

    # Connect Local (Skip if on Vercel)
    if not os.environ.get("VERCEL"):
        try:
            local_engine = create_engine(LOCAL_DB_URL)
            with local_engine.connect() as conn:
                res = conn.execute(text("SELECT 1")).scalar()
                log_message(f"✅ [LOCAL] Database Connection Successful! (Test result: {res})")
            engines["local"] = local_engine
        except Exception as e:
            log_message(f"❌ [LOCAL] Connection Failed: {e}")
    else:
        log_message("⚠️ [LOCAL] Skipping Local DB connection (Running on Vercel)")

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
def run_cell_3(create_json_files=True):
    # CELL 3: Fetch NSE Official CSV & Initialize Master Skeleton (Strict Raw Mode)
    
    # ==========================================
    # 1. CONFIGURATION
    # ==========================================
    # Official NSE Equity List
    NSE_CSV_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

    # DATA_DIR is now global
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
            if create_json_files:
                with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4)
                log_message(f"🎉 SKELETON CREATED: {OUTPUT_FILE}")
            else:
                log_message(f"🎉 SKELETON CREATED (In-Memory)")
            log_message(f"🔹 Total Items in File : {len(data)}")
            log_message("="*50)
            return data
        return []

    return main()

# --- CELL 4: NSE Indices List Fetcher ---
def run_cell_4(create_json_files=True):
    # CELL 4: NSE Indices List Fetcher (JSON + Postgres + Schema Reset)
    # DEPENDENCY: Requires Cell 1 (Database Connection) to be run first.

    # ==========================================
    # 1. CONFIGURATION & TOGGLES
    # ==========================================
    STORE_IN_JSON = create_json_files       
    STORE_IN_POSTGRES = True   

    # File Config (Using Global DATA_DIR and LOG_DIR)
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
        
        try:
            log_message(f"🌐 Fetching Home: {URL_HOME}")
            response_home = session.get(URL_HOME, timeout=15)
            if response_home.status_code != 200: 
                log_message(f"⚠️ Home Page Failed: {response_home.status_code}")
                return None
            
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
            else:
                log_message(f"⚠️ API Failed: {response_api.status_code}")
                return None
        except Exception as e:
            log_message(f"❌ Exception: {str(e)}")
            return None

    def main():
        data = fetch_indices_manual()
        if data:
            if STORE_IN_JSON:
                with open(OUTPUT_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4)
                log_message(f"✅ Saved JSON: {OUTPUT_FILE}")
            if STORE_IN_POSTGRES:
                save_to_postgres(data)
            return data
        else:
            log_message("❌ FAILURE: Could not fetch data.")
            # FALLBACK: Ensure table exists so downstream cells don't crash
            if STORE_IN_POSTGRES:
                ensure_table_exists()
            return []

    return main()

# --- CELL 5: High-Speed Concurrent NSE Index Fetcher ---
def run_cell_5(create_json_files=True, input_data=None):
    # CELL 5: High-Speed Concurrent NSE Index Fetcher
    
    # ==========================================
    # 1. CONFIGURATION
    # ==========================================
    # Use global DATA_DIR
    OUTPUT_DIR = os.path.join(DATA_DIR, 'indices')
    # LOG_FILE = 'logs/fast_fetch.log' # Using global logger

    # CONCURRENCY SETTINGS
    # ⚠️ WARNING: Higher workers = Faster speed, but HIGHER risk of IP Ban.
    # Recommended: 3 to 5. Do not go above 10.
    MAX_WORKERS = 4 

    if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)
    if not os.path.exists(LOG_DIR): os.makedirs(LOG_DIR)

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
        # safe_name = index_name.replace(' ', '_') # No longer needed here
        url = f"{URL_DETAILS_API}?index={index_name.replace(' ', '%20').replace('&', '%26')}"
        
        try:
            # Random delay to be human-like
            time.sleep(random.uniform(0.5, 1.5))
            
            response = session.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                # Return data for in-memory use with ORIGINAL name as key
                return {index_name: data}
            else:
                log_message(f"⚠️ Failed {index_name}: {response.status_code}")
        except Exception as e:
            log_message(f"❌ Error {index_name}: {e}")
        return None

    # ==========================================
    # 4. MAIN ORCHESTRATOR
    # ==========================================
    def main():
        if not init_session():
            log_message("❌ CRITICAL: Could not initialize NSE Session. Aborting.")
            return {}

        indices_list = []
        # Get list of indices from input or file
        if input_data:
            indices_list = input_data
            log_message(f"🔹 Using {len(indices_list)} indices from memory.")
        else:
            indices_file = os.path.join(DATA_DIR, 'NSE_Indices_List.json')
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
        
        # Save to Disk if requested
        if create_json_files:
            for index_name, data in results_data.items():
                # Sanitize filename for Windows (replace : and space)
                safe_name = index_name.replace(' ', '_').replace(':', '_')
                file_path = os.path.join(OUTPUT_DIR, f"{safe_name}.json")
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4)
            log_message(f"✅ Saved {len(results_data)} index files to {OUTPUT_DIR}")
        else:
            log_message(f"✅ Fetched {len(results_data)} indices (In-Memory)")

        return results_data

    return main()

# --- CELL 6: The Local Enricher ---
def run_cell_6(create_json_files=True, input_data=None, indices_data=None):
    # CELL 6: The Local Enricher (In-Place Update of Master JSON)
    
    # ==========================================
    # 1. CONFIGURATION
    # ==========================================
    # We read AND write to the same file to keep it as the Single Source of Truth
    MASTER_FILE = os.path.join(DATA_DIR, 'NSE_STOCKS_SKELETON.json')
    INDICES_DIR = os.path.join(DATA_DIR, 'indices')

    # ==========================================
    # 2. BUILD LOOKUP MAP (The "Reverse Index")
    # ==========================================
    def build_indices_lookup():
        log_message(f"📖 Scanning Index Data to build Lookup Map...")
        
        # Map: Symbol -> { "meta": ..., "indices": [list of index names], "web_data": ... }
        lookup_map = defaultdict(lambda: {"meta": None, "indices": []})
        
        # SOURCE 1: In-Memory Data (Preferred)
        if indices_data:
            source_items = indices_data.items()
            log_message(f"🔹 Using {len(source_items)} indices from memory.")
        # SOURCE 2: File System (Fallback)
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

        # Process the data (Common Logic)
        for index_name, content in source_items:
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

    # ==========================================
    # 3. UPDATE MASTER JSON IN-PLACE
    # ==========================================
    def update_master_file(lookup_map):
        master_list = []
        
        # 1. Load Data (Input or File)
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
                stock_obj["flags"]["is_fno"] = meta.get("isFNOSec", False)
                stock_obj["flags"]["is_etf"] = meta.get("isETFSec", False)
                stock_obj["flags"]["is_slb_eligible"] = meta.get("isSLBSec", False)
                stock_obj["flags"]["is_suspended"] = meta.get("isSuspended", False)
                stock_obj["flags"]["is_delisted"] = meta.get("isDelisted", False)
                stock_obj["flags"]["is_municipal_bond"] = meta.get("isMunicipalBond", False)
                stock_obj["flags"]["is_mtf_eligible"] = False 
                
                # --- 5. ADD RELATIONS ---
                stock_obj["relations"] = {
                    "indices": indices_list
                }
                
                enriched_count += 1
            else:
                stock_obj["relations"] = {"indices": []}

        # --- SAVE BACK TO SAME FILE ---
        if create_json_files:
            log_message(f"💾 Saving updates to {MASTER_FILE}...")
            with open(MASTER_FILE, 'w', encoding='utf-8') as f:
                json.dump(master_list, f, indent=4)
            log_message(f"🔹 File Updated: {MASTER_FILE}")
        else:
            log_message(f"💾 Updates applied in-memory.")

        log_message(f"\\n" + "="*50)
        log_message(f"✅ UPDATE COMPLETE")
        log_message(f"🔹 Stocks Enriched: {enriched_count}/{len(master_list)}")
        log_message("="*50)
        
        return master_list

    # ==========================================
    # 4. EXECUTION
    # ==========================================
    lookup_map = build_indices_lookup()
    if lookup_map:
        return update_master_file(lookup_map)
    return []

# --- CELL 7: The Token Hunter ---
def run_cell_7(create_json_files=True, input_data=None):
    # CELL 7: The Token Hunter (Final Fixed Headers & Logic)
    
    # ==========================================
    # 1. CONFIGURATION
    # ==========================================
    # File Config (Using Global DATA_DIR and LOG_DIR)
    JSON_PATH = os.path.join(DATA_DIR, 'NSE_STOCKS_SKELETON.json')
    SEARCH_URL = "https://global-search-bbe-prod.angelone.in/v5/search"
    MAX_WORKERS = 5 # Reduced from 10 to avoid rate limits/timeouts 

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
        cleaned = re.sub(r'\s+\(?(Limited|Ltd|India|LTD|LIMITED|INDIA|Plc|Corp|Corporation)\)?\.?$', '', name, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s+\([^)]*\)', '', cleaned)
        return cleaned.strip()

    def bulk_log_to_db(logs_list):
        """Inserts multiple logs into the DB in one transaction."""
        if not logs_list: return
        
        engine = engines.get("neon") or engines.get("local")
        if not engine: return

        try:
            # Prepare data for bulk insert
            records = [
                {
                    "level": l["level"],
                    "source": l["source"],
                    "event": l["event"],
                    "message": l["message"],
                    "context": l["context"] or {}
                }
                for l in logs_list
            ]
            
            stmt = insert(Table('system_logs', MetaData(), autoload_with=engine))
            with engine.begin() as conn:
                conn.execute(stmt, records)
        except Exception as e:
            log_message(f"⚠️ Bulk DB Log Failed: {e}")

    def perform_search(keyword, log_buffer=None):
        """Hits Angel API. Logs are appended to log_buffer if provided."""
        session = get_session()
        # Check if user updated the cookie
        if "YOUR_HUGE_COOKIE" in session.headers['Cookie']:
            log_message("❌ ERROR: You forgot to paste the fresh Cookie in CONFIGURATION!")
            raise ValueError("Invalid Cookie")

        headers = session.headers.copy()
        headers["X-Requestid"] = str(uuid.uuid4())
        
        SEARCH_URL = "https://global-search-bbe-prod.angelone.in/v5/search"
        params = {
            "userType": "B2C",
            "accountType": "D",
            "keyword": keyword
        }
        
        try:
            # BUFFER LOGS instead of writing directly
            if log_buffer is not None:
                log_buffer.append({
                    "level": "INFO",
                    "source": "angel_api",
                    "event": "search",
                    "message": f"Searching: {keyword}",
                    "context": {"url": SEARCH_URL, "keyword": keyword}
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
        local_logs = [] # Capture logs for this thread
        
        # Skip if already done
        if stock_obj["trading_info"].get("token_nse") and stock_obj["__RAW_DATA_SOURCES__"].get("source_angel_api"):
            return stock_obj, local_logs

        identifiers = stock_obj.get("identifiers", {})
        symbol = identifiers.get("symbol")
        company_name = identifiers.get("company_name", "").strip()
        
        if not symbol or not company_name: return stock_obj, local_logs

        match = None
        tried_queries = []

        # --- 1. Search by Symbol ---
        search_query = symbol
        tried_queries.append(search_query)
        results = perform_search(search_query, log_buffer=local_logs)
        match = parse_best_match(results, search_symbol=symbol)

        # --- 2. Search by FULL Company Name (Browser Style) ---
        if not match:
            search_query = company_name
            tried_queries.append(search_query)
            results = perform_search(search_query, log_buffer=local_logs)
            match = parse_best_match(results, search_symbol=symbol)

        # --- 3. Search by Clean Name ---
        if not match:
            cleaned_name = clean_company_name(company_name)
            if cleaned_name != company_name:
                search_query = cleaned_name
                tried_queries.append(search_query)
                results = perform_search(search_query, log_buffer=local_logs)
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

        return stock_obj, local_logs

    # ==========================================
    # 4. EXECUTION
    # ==========================================
    def main():
        master_list = []
        
        # 1. Load Data (Input or File)
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

        # Clear logs
        if create_json_files:
            open(MISSING_LOG_PATH, 'w').close()

        pending = [s for s in master_list if not s["trading_info"].get("token_nse")]
        log_message(f"🕵️ Sweeping {len(pending)} missing stocks...")
        
        if len(pending) == 0:
            log_message("✅ No missing stocks to process!")
            return master_list

        updated_list = []
        all_logs = []
        
        # Using workers
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Map returns (stock_obj, logs) tuples now
            results = list(tqdm(executor.map(fetch_and_enrich_stock, master_list), total=len(master_list)))
            
            for stock, logs in results:
                updated_list.append(stock)
                all_logs.extend(logs)

        # BULK LOGGING
        if all_logs:
            log_message(f"📝 Bulk inserting {len(all_logs)} API logs to DB...")
            bulk_log_to_db(all_logs)

        success_count = sum(1 for s in updated_list if s["trading_info"].get("token_nse"))

        if create_json_files:
            log_message(f"💾 Saving...")
            with open(JSON_PATH, 'w', encoding='utf-8') as f:
                json.dump(updated_list, f, indent=4)
            log_message(f"🔹 Check logs at: {MISSING_LOG_PATH}")

        log_message(f"\\n" + "="*50)
        log_message(f"✅ COMPLETE")
        log_message(f"🔹 Total Tokens Found: {success_count}")
        log_message("="*50)
        
        return updated_list

    return main()

# --- CELL 8: Stocks Master Seeder ---
def run_cell_8(create_json_files=True, input_data=None):
    # CELL 8: Stocks Master Seeder (JSON -> Postgres)
    # DEPENDENCY: Requires Cell 1 (Engine) and Cell 4 (Indices Table Populated)
    
    # ==========================================
    # 1. CONFIGURATION
    # ==========================================
    # File Config (Using Global DATA_DIR)
    JSON_SOURCE = os.path.join(DATA_DIR, 'NSE_STOCKS_SKELETON.json')

    # ==========================================
    # 2. SCHEMA DEFINITION
    # ==========================================
    def init_schema(engine):
        metadata = MetaData()
        
        # Reflect existing 'indices' table to allow Foreign Key linking
        # Reflect existing 'indices' table to allow Foreign Key linking
        inspector = inspect(engine)
        if not inspector.has_table("indices"):
             log_message(f"❌ CRITICAL ERROR: Table 'indices' not found. Run Cell 4 first!")
             raise Exception("Table 'indices' not found")
        
        indices_table = Table('indices', metadata, autoload_with=engine)
        log_message("✅ Detected existing 'indices' table.")

        # --- TABLE: STOCKS ---
        stocks_table = Table(
            'stocks', metadata,
            Column('id', Integer, primary_key=True, autoincrement=True),
            Column('master_key', String),
            Column('symbol', String, unique=True, nullable=False), # Unique Key
            Column('isin', String),
            Column('slb_isin', String),
            Column('company_name', String),
            
            # SERIES FLAGS
            Column('is_eq', Boolean, default=False),
            Column('is_be', Boolean, default=False),
            Column('is_bl', Boolean, default=False),
            Column('is_bz', Boolean, default=False),
            
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
            Column('stock_id', Integer, ForeignKey('stocks.id', ondelete="CASCADE")),
            Column('index_id', Integer, ForeignKey('indices.id', ondelete="CASCADE")),
            UniqueConstraint('stock_id', 'index_id', name='uq_stock_index_pair')
        )

        # --- TABLE: SYSTEM_LOGS ---
        system_logs = Table(
            'system_logs', metadata,
            Column('id', BigInteger, primary_key=True, autoincrement=True),
            Column('timestamp', DateTime, default=datetime.datetime.utcnow),
            Column('level', String(10), index=True),   # INFO, ERROR, WARN
            Column('source', String(50), index=True),  # e.g., "cron", "api", "angel_one"
            Column('event', String(50)),               # e.g., "seed_start", "stock_processed"
            Column('message', Text),
            Column('context', JSON)                    # Flexible JSON for metadata
        )
        
        # Safety Net & Schema Validation
        # Check if table exists but has wrong schema (missing stock_id)
        inspector = inspect(engine)
        if inspector.has_table("stock_indices_map"):
            columns = [c['name'] for c in inspector.get_columns("stock_indices_map")]
            if "stock_id" not in columns:
                log_message("⚠️ Detected incompatible schema for 'stock_indices_map'. Dropping table to recreate...")
                with engine.begin() as conn:
                    conn.execute(text("DROP TABLE stock_indices_map CASCADE"))
        
        metadata.create_all(engine)
        log_message("✅ Database Schema (stocks, stock_indices_map) Verified.")
        return stocks_table, stock_indices_map

    # ==========================================
    # 3. SEEDER LOGIC
    # ==========================================
    def seed_database():
        stock_list = []
        if input_data:
            stock_list = input_data
            log_message(f"📖 Loaded {len(stock_list)} stocks from memory.")
        elif os.path.exists(JSON_SOURCE):
            log_message(f"📖 Loading Master JSON from {JSON_SOURCE}...")
            with open(JSON_SOURCE, 'r', encoding='utf-8') as f:
                stock_list = json.load(f)
        else:
            log_message(f"❌ Error: No input data and {JSON_SOURCE} not found.")
            return

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
                    
                    # 2. Fetch Stock ID Map (Symbol -> ID)
                    stock_id_map = {}
                    result = conn.execute(text("SELECT symbol, id FROM stocks"))
                    for row in result:
                        stock_id_map[row[0]] = row[1]
                    
                    # 3. Prepare Relations with IDs
                    relation_records_batch = []
                    for item in stock_list:
                        stock_symbol = item.get("master_key")
                        stock_id = stock_id_map.get(stock_symbol)
                        if not stock_id: continue
                        
                        stock_indices = item.get("relations", {}).get("indices", [])
                        for index_name in stock_indices:
                            idx_id = index_map.get(index_name.upper())
                            if idx_id:
                                rel_record = {
                                    "stock_id": stock_id,
                                    "index_id": idx_id
                                }
                                relation_records_batch.append(rel_record)

                    # 4. Clear Old Relations & Bulk Insert New
                    if relation_records_batch:
                        log_message(f"🧹 [{name.upper()}] Clearing old relations...")
                        conn.execute(text("DELETE FROM stock_indices_map")) # Full clear for fresh state
                        
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

@app.get("/logs", response_class=HTMLResponse)
def view_logs(limit: int = 200):
    """Fetches latest system logs and renders them in a beautiful UI."""
    engine = engines.get("neon") or engines.get("local")
    if not engine:
        return HTMLResponse("<h1>❌ No Database Connection Available</h1>", status_code=500)

    try:
        with engine.connect() as conn:
            # Check if table exists first
            inspector = inspect(engine)
            if not inspector.has_table("system_logs"):
                return HTMLResponse("<h1>⚠️ 'system_logs' table does not exist yet.</h1><p>Wait for server startup or run a search to generate logs.</p>")
                
            result = conn.execute(text("SELECT * FROM system_logs ORDER BY timestamp DESC LIMIT :limit"), {"limit": limit})
            logs = result.fetchall()
    except Exception as e:
        return HTMLResponse(f"<h1>❌ Database Error</h1><p>{e}</p>", status_code=500)

    # --- HTML TEMPLATE ---
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>System Logs | Finance Server</title>
        <style>
            :root {{
                --bg-color: #0f172a;
                --card-bg: #1e293b;
                --text-primary: #f1f5f9;
                --text-secondary: #94a3b8;
                --border-color: #334155;
                --accent: #3b82f6;
                --success: #22c55e;
                --warning: #eab308;
                --error: #ef4444;
            }}
            body {{
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
                background-color: var(--bg-color);
                color: var(--text-primary);
                margin: 0;
                padding: 20px;
            }}
            .container {{
                max-width: 1400px;
                margin: 0 auto;
            }}
            header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 20px;
                padding-bottom: 20px;
                border-bottom: 1px solid var(--border-color);
            }}
            h1 {{ margin: 0; font-size: 1.5rem; }}
            .refresh-btn {{
                background-color: var(--accent);
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 6px;
                cursor: pointer;
                font-weight: 500;
                text-decoration: none;
                transition: opacity 0.2s;
            }}
            .refresh-btn:hover {{ opacity: 0.9; }}
            
            .table-container {{
                background-color: var(--card-bg);
                border-radius: 8px;
                border: 1px solid var(--border-color);
                overflow-x: auto;
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 0.875rem;
            }}
            th, td {{
                padding: 12px 16px;
                text-align: left;
                border-bottom: 1px solid var(--border-color);
            }}
            th {{
                background-color: #0f172a;
                color: var(--text-secondary);
                font-weight: 600;
                position: sticky;
                top: 0;
            }}
            tr:last-child td {{ border-bottom: none; }}
            tr:hover {{ background-color: rgba(255,255,255,0.02); }}
            
            /* Badges */
            .badge {{
                padding: 2px 8px;
                border-radius: 9999px;
                font-size: 0.75rem;
                font-weight: 600;
            }}
            .badge-INFO {{ background-color: rgba(59, 130, 246, 0.1); color: var(--accent); }}
            .badge-WARNING {{ background-color: rgba(234, 179, 8, 0.1); color: var(--warning); }}
            .badge-ERROR {{ background-color: rgba(239, 68, 68, 0.1); color: var(--error); }}
            
            .timestamp {{ color: var(--text-secondary); white-space: nowrap; font-family: monospace; }}
            .source {{ font-weight: 500; color: #e2e8f0; }}
            .event {{ color: var(--text-secondary); }}
            .context {{ 
                font-family: monospace; 
                color: var(--text-secondary); 
                font-size: 0.75rem; 
                max-width: 300px; 
                overflow: hidden; 
                text-overflow: ellipsis; 
                white-space: nowrap;
            }}
            .context:hover {{
                white-space: pre-wrap;
                max-width: none;
                word-break: break-all;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <div>
                    <h1>System Logs</h1>
                    <div style="color: var(--text-secondary); font-size: 0.875rem; margin-top: 4px;">
                        Showing latest {len(logs)} entries from <strong>{engine.url.database}</strong>
                    </div>
                </div>
                <a href="/logs?limit={limit}" class="refresh-btn">🔄 Refresh</a>
            </header>
            
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th width="180">Timestamp</th>
                            <th width="80">Level</th>
                            <th width="120">Source</th>
                            <th width="120">Event</th>
                            <th>Message</th>
                            <th width="250">Context</th>
                        </tr>
                    </thead>
                    <tbody>
    """
    
    for log in logs:
        # Parse row
        # Row structure depends on DB driver, usually tuple or object
        # system_logs columns: id, timestamp, level, source, event, message, context
        
        # Handle different result types (tuple vs mapping)
        try:
            ts = log.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            lvl = log.level
            src = log.source
            evt = log.event
            msg = log.message
            ctx = json.dumps(log.context) if log.context else ""
        except:
            # Fallback for tuple access if object access fails
            ts = str(log[1])
            lvl = log[2]
            src = log[3]
            evt = log[4]
            msg = log[5]
            ctx = str(log[6]) if log[6] else ""

        html_content += f"""
                        <tr>
                            <td class="timestamp">{ts}</td>
                            <td><span class="badge badge-{lvl}">{lvl}</span></td>
                            <td class="source">{src}</td>
                            <td class="event">{evt}</td>
                            <td>{msg}</td>
                            <td class="context" title='{ctx}'>{ctx}</td>
                        </tr>
        """

    html_content += """
                    </tbody>
                </table>
            </div>
        </div>
    </body>
    </html>
    """
    return html_content

@app.on_event("startup")
def startup_event():
    # Initialize DB Connection on startup
    engines = run_cell_1()
    
    # Initialize Logging DB (Neon preferred)
    target_engine = engines.get("neon") or engines.get("local")
    if target_engine:
        init_logging_db(target_engine)
        
    log_message("🚀 Server Started! Access it here: http://localhost:8000")

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
def seed_stocks_full_refresh(background_tasks: BackgroundTasks, createJSONFiles: bool = True):
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
        # 1. Master Skeleton
        master_list = run_cell_3(create_json_files=createJSONFiles)
        
        # 2. Indices List (Independent)
        indices_list = run_cell_4(create_json_files=createJSONFiles)
        
        # 3. Indices Details (Concurrent)
        indices_data = run_cell_5(create_json_files=createJSONFiles, input_data=indices_list)
        
        # 4. Enrich Master with Indices (In-Memory)
        master_list = run_cell_6(create_json_files=createJSONFiles, input_data=master_list, indices_data=indices_data)
        
        # 5. Token Hunt (In-Memory)
        master_list = run_cell_7(create_json_files=createJSONFiles, input_data=master_list)
        
        # 6. Seed DB (In-Memory)
        run_cell_8(create_json_files=createJSONFiles, input_data=master_list)
    
    background_tasks.add_task(full_pipeline)
    return {"status": "accepted", "message": f"Full refresh started (createJSONFiles={createJSONFiles})"}

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

@app.delete("/clean/indices")
def clean_indices():
    """Truncates the 'indices' table."""
    results = {}
    for name, engine in engines.items():
        if engine is None: continue
        try:
            with engine.begin() as conn:
                conn.execute(text("TRUNCATE TABLE indices CASCADE"))
            results[name] = "Success"
        except Exception as e:
            results[name] = str(e)
    return {"status": "completed", "results": results}

@app.delete("/clean/stocks")
def clean_stocks():
    """Truncates 'stocks' and 'stock_indices_map' tables."""
    results = {}
    for name, engine in engines.items():
        if engine is None: continue
        try:
            with engine.begin() as conn:
                conn.execute(text("TRUNCATE TABLE stocks CASCADE"))
            results[name] = "Success"
        except Exception as e:
            results[name] = str(e)
    return {"status": "completed", "results": results}

@app.delete("/clean/stock-indices-map")
def clean_map():
    """Truncates 'stock_indices_map' table."""
    results = {}
    for name, engine in engines.items():
        if engine is None: continue
        try:
            with engine.begin() as conn:
                conn.execute(text("TRUNCATE TABLE stock_indices_map CASCADE"))
            results[name] = "Success"
        except Exception as e:
            results[name] = str(e)
    return {"status": "completed", "results": results}

@app.delete("/clean/all")
def clean_all(delete_all_tables: bool = False):
    """
    Cleans all 3 tables: indices, stocks, stock_indices_map.
    If delete_all_tables=True, DROPS the tables.
    If False (default), TRUNCATES them.
    """
    results = {}
    for name, engine in engines.items():
        if engine is None: continue
        try:
            with engine.begin() as conn:
                if delete_all_tables:
                    conn.execute(text("DROP TABLE IF EXISTS stock_indices_map CASCADE"))
                    conn.execute(text("DROP TABLE IF EXISTS stocks CASCADE"))
                    conn.execute(text("DROP TABLE IF EXISTS indices CASCADE"))
                    action = "Dropped"
                else:
                    conn.execute(text("TRUNCATE TABLE stock_indices_map CASCADE"))
                    conn.execute(text("TRUNCATE TABLE stocks CASCADE"))
                    conn.execute(text("TRUNCATE TABLE indices CASCADE"))
                    action = "Truncated"
            results[name] = f"{action} Success"
        except Exception as e:
            results[name] = str(e)
    return {"status": "completed", "results": results}

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
