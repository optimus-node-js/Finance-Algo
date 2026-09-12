import os
from sqlalchemy import create_engine
from app.core.config import settings
from app.core.logging import log_message

# Global Engines Registry
engines = {"local": None, "neon": None, "supabase": None}

def get_db_engines():
    """Initializes and returns the database engines."""
    global engines
    
    # 1. Local PostgreSQL
    try:
        local_db_url = f"postgresql://{settings.LOCAL_DB_USER}:{settings.LOCAL_DB_PASS}@{settings.LOCAL_DB_HOST}:{settings.LOCAL_DB_PORT}/{settings.LOCAL_DB_NAME}"
        engines["local"] = create_engine(local_db_url)
        with engines["local"].connect() as conn:
            pass
        log_message("✅ [LOCAL] Database Connected!")
    except Exception as e:
        log_message(f"⚠️ [LOCAL] Connection Failed: {e}")
        engines["local"] = None

    # 2. Neon DB (Cloud)
    if settings.NEON_DB_URL:
        try:
            engines["neon"] = create_engine(settings.NEON_DB_URL)
            with engines["neon"].connect() as conn:
                pass
            log_message("✅ [NEON] Database Connected!")
        except Exception as e:
            log_message(f"⚠️ [NEON] Connection Failed: {e}")
            engines["neon"] = None
    else:
        log_message("⚠️ [NEON] No connection string provided in .env")

    # 3. Supabase (Cloud)
    if settings.SUPABASE_DB_URL:
        try:
            engines["supabase"] = create_engine(settings.SUPABASE_DB_URL)
            with engines["supabase"].connect() as conn:
                pass
            log_message("✅ [SUPABASE] Database Connected!")
        except Exception as e:
            log_message(f"⚠️ [SUPABASE] Connection Failed: {e}")
            engines["supabase"] = None
    else:
        log_message("⚠️ [SUPABASE] No connection string provided in .env")

    return engines


def get_raw_connection(which: str = "local"):
    """Returns a raw DBAPI (psycopg2) connection from one of the SQLAlchemy
    engines above, for services that use psycopg2 cursors directly
    (paper_trading, scanner_scheduler, market_data callers) instead of the
    ORM. Initializes the engines on first use if they haven't been yet."""
    if engines.get(which) is None:
        get_db_engines()
    engine = engines.get(which)
    if engine is None:
        raise RuntimeError(f"No '{which}' database engine available - check your .env")
    return engine.raw_connection()
