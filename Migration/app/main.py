from fastapi import FastAPI
from app.routers import (
    seed_router, clean_router, logs_router,
    market_router, trading_router, portfolio_router,
    live_data_router, account_router,
)
from app.db import get_db_engines
from app.core import init_logging_db, log_message
from app.services.bhavcopy import sync_incremental
from app.services.scanner_scheduler import scanner_service

app = FastAPI(
    title="Algotrading API",
    description="Data seeding, live market scanning, and paper-trading engine for the Nifty 500 Gap+VolShock strategy",
)

# Include Routers
app.include_router(seed_router)
app.include_router(clean_router)
app.include_router(logs_router)
app.include_router(market_router)
app.include_router(trading_router)
app.include_router(portfolio_router)
app.include_router(live_data_router)
app.include_router(account_router)

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
        
    # Daily bhavcopy incremental sync -- independent of whether the trading
    # scanner is running, so market_data_daily stays current regardless.
    # Reuses scanner_service's APScheduler instance rather than spinning up
    # a second one.
    if not scanner_service.scheduler.running:
        scanner_service.scheduler.start()
    scanner_service.scheduler.add_job(sync_incremental, "cron", hour=16, minute=30, id="bhavcopy_sync",
                                       replace_existing=True)

    log_message("🚀 Server Started! Access it here: http://localhost:8000")

@app.get("/")
def read_root():
    return {"message": "Migration API is running (Modular). Use /docs to see endpoints."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
