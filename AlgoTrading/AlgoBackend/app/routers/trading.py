from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.services.scanner_scheduler import scanner_service

router = APIRouter(prefix="/trading", tags=["Trading"])


@router.post("/scanner/start")
def start_scanner(background_tasks: BackgroundTasks):
    try:
        response, should_load = scanner_service.request_start()
        if should_load:
            background_tasks.add_task(scanner_service._start_blocking)
        return response
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/scanner/stop")
def stop_scanner():
    return scanner_service.stop()


@router.get("/scanner/status")
def scanner_status():
    return scanner_service.status()


@router.get("/positions")
def open_positions():
    if not scanner_service.engine:
        return {}
    return {
        symbol: {"qty": p.qty, "entry_price": p.entry_price, "opened_at": p.opened_at.isoformat()}
        for symbol, p in scanner_service.engine.open_positions.items()
    }
