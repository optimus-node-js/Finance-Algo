import os
from fastapi import APIRouter
from sqlalchemy import text
from app.db import get_db_engines
from app.core import settings, log_message

router = APIRouter(tags=["Cleanup"])

@router.delete("/clean/indices")
def clean_indices():
    """Truncates the 'indices' table."""
    results = {}
    engines = get_db_engines()
    for name, engine in engines.items():
        if engine is None: continue
        try:
            with engine.begin() as conn:
                conn.execute(text("TRUNCATE TABLE indices CASCADE"))
            results[name] = "Success"
        except Exception as e:
            results[name] = str(e)
    return {"status": "completed", "results": results}

@router.delete("/clean/stocks")
def clean_stocks():
    """Truncates 'stocks' and 'stock_indices_map' tables."""
    results = {}
    engines = get_db_engines()
    for name, engine in engines.items():
        if engine is None: continue
        try:
            with engine.begin() as conn:
                conn.execute(text("TRUNCATE TABLE stocks CASCADE"))
            results[name] = "Success"
        except Exception as e:
            results[name] = str(e)
    return {"status": "completed", "results": results}

@router.delete("/clean/stock-indices-map")
def clean_map():
    """Truncates 'stock_indices_map' table."""
    results = {}
    engines = get_db_engines()
    for name, engine in engines.items():
        if engine is None: continue
        try:
            with engine.begin() as conn:
                conn.execute(text("TRUNCATE TABLE stock_indices_map CASCADE"))
            results[name] = "Success"
        except Exception as e:
            results[name] = str(e)
    return {"status": "completed", "results": results}

@router.delete("/clean/all")
def clean_all(delete_all_tables: bool = False):
    """
    Cleans all 3 tables: indices, stocks, stock_indices_map.
    If delete_all_tables=True, DROPS the tables.
    If False (default), TRUNCATES them.
    """
    results = {}
    engines = get_db_engines()
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

@router.delete("/cleanup")
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
    if os.path.exists(settings.LOG_DIR):
        for f in os.listdir(settings.LOG_DIR):
            file_path = os.path.join(settings.LOG_DIR, f)
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    deleted_files.append(file_path)
            except Exception as e:
                errors.append(f"Failed to delete {file_path}: {e}")

    # 2. Clean Indices Data
    indices_dir = os.path.join(settings.DATA_DIR, 'indices')
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
    if os.path.exists(settings.DATA_DIR):
        for f in os.listdir(settings.DATA_DIR):
            if f.endswith(".json") and "OpenAPIScriptMaster" not in f:
                file_path = os.path.join(settings.DATA_DIR, f)
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
