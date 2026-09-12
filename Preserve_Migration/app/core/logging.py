import os
import datetime
import logging
from sqlalchemy import MetaData, Table, Column, String, Text, JSON, BigInteger, DateTime
from sqlalchemy.dialects.postgresql import insert
from app.core.config import settings

# Setup Basic Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger()

GLOBAL_LOG_FILE = os.path.join(settings.LOG_DIR, 'app.log')

def log_message(msg, console=True):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"[{timestamp}] {msg}"
    
    # 1. Write to File
    try:
        if not os.path.exists(settings.LOG_DIR): os.makedirs(settings.LOG_DIR)
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

def log_to_db(engines, level, source, event, message, context=None):
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

def bulk_log_to_db(engines, logs_list):
    """Inserts multiple logs into the DB in one transaction."""
    if not logs_list: return
    
    # Prepare data for bulk insert once
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
    
    # Write to BOTH databases
    for db_name in ["local", "neon"]:
        engine = engines.get(db_name)
        if not engine:
            continue
            
        try:
            stmt = insert(Table('system_logs', MetaData(), autoload_with=engine))
            with engine.begin() as conn:
                conn.execute(stmt, records)
            log_message(f"✅ Successfully inserted {len(records)} logs to {db_name.upper()} DB")
        except Exception as e:
            log_message(f"⚠️ Bulk DB Log Failed for {db_name.upper()}: {e}")
            import traceback
            log_message(f"Traceback: {traceback.format_exc()}")
