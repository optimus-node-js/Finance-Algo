from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException
from app.services import (
    fetch_nse_equity_list,
    fetch_nse_indices_list,
    fetch_sector_classification,
    enrich_stocks_with_tokens,
    seed_stocks_database
)
from app.db.session import get_raw_connection
from app.services.bhavcopy import run_backfill_tracked, sync_incremental

router = APIRouter(prefix="/seed", tags=["Seeding"])

@router.post("/indices")
def seed_indices_endpoint():
    """
    Runs Cell 4: Fetch Indices Manual & Update DB
    """
    try:
        fetch_nse_indices_list()
        return {"status": "success", "message": "Indices seeded successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/stocks/full-refresh")
def seed_stocks_full_refresh(background_tasks: BackgroundTasks, createJSONFiles: bool = True):
    """
    Runs the full pipeline against the REAL schema (instruments/sectors/
    index_constituents):
    1. Fetch NSE equity CSV (full universe, ~2400 symbols)
    2. Fetch indices list -> `indices` table
    3. Sector classification via static NSE archive CSVs (Nifty Total Market +
       Microcap 250 -- ~1000 symbols get real sector data; the old dynamic
       per-index-detail fetch is dropped, NSE's website blocks non-browser
       requests, confirmed live via HTTP 403)
    4. Token hunt via Angel One's official public scrip master (no cookie,
       no rate limit)
    5. Seed DB -> `instruments` / `sectors` / `index_constituents`
    """
    def full_pipeline():
        master_list = fetch_nse_equity_list(create_json_files=createJSONFiles)
        fetch_nse_indices_list(create_json_files=createJSONFiles)

        sector_map = fetch_sector_classification()
        for stock in master_list:
            symbol = stock.get("identifiers", {}).get("symbol")
            if symbol in sector_map:
                stock["classification"]["industry"] = sector_map[symbol]

        master_list = enrich_stocks_with_tokens(create_json_files=createJSONFiles, input_data=master_list)
        seed_stocks_database(create_json_files=createJSONFiles, input_data=master_list)

    background_tasks.add_task(full_pipeline)
    return {"status": "accepted", "message": f"Full refresh started (createJSONFiles={createJSONFiles})"}

@router.post("/stocks/quick-update")
def seed_stocks_quick_update(background_tasks: BackgroundTasks):
    """
    Runs a quick update (skipping heavy token fetch if possible, or just DB seed)
    For now, let's just run Cell 8 (Seed DB from existing JSON)
    """
    def quick_pipeline():
        seed_stocks_database()

    background_tasks.add_task(quick_pipeline)
    return {"status": "accepted", "message": "Quick update (DB Seed) started in background"}


@router.post("/backfill-history")
def backfill_history(background_tasks: BackgroundTasks, years: int = 10, start_date: str | None = None, end_date: str | None = None):
    """
    Backfills market_data_daily from NSE bhavcopy (one file/day, full market,
    no Angel One API involved -- see app/services/bhavcopy.py for why).
    Handles both NSE archive formats automatically (modern CSV from
    2019-09-30 onward, legacy zip before that -- confirmed live, no
    delivery data in the legacy format so those columns stay NULL for older
    rows). Defaults to the last `years` years; pass start_date/end_date
    (YYYY-MM-DD) for a specific range. Progress trackable via GET
    /seed/sync-status while it runs.
    """
    end = date.fromisoformat(end_date) if end_date else date.today()
    start = date.fromisoformat(start_date) if start_date else end - timedelta(days=365 * years)

    background_tasks.add_task(run_backfill_tracked, "bhavcopy_backfill", start, end)
    return {"status": "accepted", "message": f"Backfill started for {start} to {end} (runs in background)"}


@router.post("/sync-incremental")
def sync_incremental_endpoint(background_tasks: BackgroundTasks):
    """
    Catches market_data_daily up from wherever the last sync left off
    (any gap size) through today. Meant to run daily -- also registered as
    an APScheduler cron job in scanner_scheduler.py (16:30 IST, after
    bhavcopy publishes and after market close) so this happens automatically
    without needing to hit this endpoint manually every day.
    """
    background_tasks.add_task(sync_incremental)
    return {"status": "accepted", "message": "Incremental sync started in background"}


@router.get("/sync-status")
def sync_status(job_name: str = "bhavcopy_backfill"):
    conn = get_raw_connection("local")
    cur = conn.cursor()
    cur.execute(
        "SELECT job_name, last_run_at, last_synced_date, status, rows_processed, days_total, days_done, last_error "
        "FROM sync_jobs WHERE job_name = %s", (job_name,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        raise HTTPException(404, f"No sync job named '{job_name}' has run yet")
    return {
        "job_name": row[0], "last_run_at": row[1].isoformat() if row[1] else None,
        "last_synced_date": str(row[2]) if row[2] else None, "status": row[3],
        "rows_processed": row[4], "days_total": row[5], "days_done": row[6], "last_error": row[7],
    }
