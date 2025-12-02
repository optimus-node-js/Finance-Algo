import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()

class Settings:
    # Environment
    VERCEL = os.environ.get("VERCEL")
    
    # Paths
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    if VERCEL:
        LOG_DIR = '/tmp/logs'
        DATA_DIR = '/tmp/data'
    else:
        LOG_DIR = os.path.join(BASE_DIR, 'logs')
        DATA_DIR = os.path.join(BASE_DIR, 'data')

    # Database - Local
    LOCAL_DB_USER = os.getenv("LOCAL_DB_USER", "postgres")
    LOCAL_DB_PASS = os.getenv("LOCAL_DB_PASS", "postgres")
    LOCAL_DB_HOST = os.getenv("LOCAL_DB_HOST", "localhost")
    LOCAL_DB_PORT = os.getenv("LOCAL_DB_PORT", "5432")
    LOCAL_DB_NAME = os.getenv("LOCAL_DB_NAME", "postgres")
    
    # Database - Neon
    NEON_DB_URL = os.getenv("NEON_DB_URL", "")
    
    # Angel One
    ANGEL_COOKIE = os.getenv("ANGEL_COOKIE", "")
    
settings = Settings()
