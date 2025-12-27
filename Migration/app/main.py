from fastapi import FastAPI
from app.routers import seed_router, clean_router, logs_router
from app.db import get_db_engines
from app.core import init_logging_db, log_message

app = FastAPI(title="Migration API", description="API for seeding stock data (Modular)")

# Include Routers
app.include_router(seed_router)
app.include_router(clean_router)
app.include_router(logs_router)

@app.on_event("startup")
def startup_event():
    # Initialize DB Connection on startup
    engines = get_db_engines()
    
    # Initialize Logging DB (Both Local and Neon)
    for name, engine in engines.items():
        if engine:
            try:
                init_logging_db(engine)
                log_message(f"✅ [{name.upper()}] Logging Table Verified.")
            except Exception as e:
                log_message(f"⚠️ [{name.upper()}] Failed to init logging table: {e}")
        
    log_message("🚀 Server Started! Access it here: http://localhost:8000")

@app.get("/")
def read_root():
    return {"message": "Migration API is running (Modular). Use /docs to see endpoints."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
