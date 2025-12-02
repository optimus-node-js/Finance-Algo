import os
from sqlalchemy import create_engine
from app.core.config import settings
from app.core.logging import log_message

# Global Engines Registry
engines = {"local": None, "neon": None}

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
        
    return engines
