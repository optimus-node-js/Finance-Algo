from fastapi import APIRouter, BackgroundTasks, HTTPException
from app.services import (
    fetch_nse_equity_list, 
    fetch_nse_indices_list, 
    fetch_nse_indices_details,
    enrich_stocks_from_indices, 
    enrich_stocks_with_tokens,
    seed_stocks_database
)

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
        master_list = fetch_nse_equity_list(create_json_files=createJSONFiles)
        
        # 2. Indices List (Independent)
        indices_list = fetch_nse_indices_list(create_json_files=createJSONFiles)
        
        # 3. Indices Details (Concurrent)
        indices_data = fetch_nse_indices_details(create_json_files=createJSONFiles, input_data=indices_list)
        
        # 4. Enrich Master with Indices (In-Memory)
        master_list = enrich_stocks_from_indices(create_json_files=createJSONFiles, input_data=master_list, indices_data=indices_data)
        
        # 5. Token Hunt (In-Memory)
        master_list = enrich_stocks_with_tokens(create_json_files=createJSONFiles, input_data=master_list)
        
        # 6. Seed DB (In-Memory)
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
