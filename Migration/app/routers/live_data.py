from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.session import get_raw_connection
from app.services.angel_auth import get_angel_session
from app.services.market_ws import LiveMarketDataManager

router = APIRouter(prefix="/live", tags=["Live Data"])

_manager: LiveMarketDataManager | None = None


class SubscribeRequest(BaseModel):
    tokens: list[str]
    mode: str = "QUOTE"


@router.post("/start")
def start_live():
    global _manager
    if _manager and _manager.is_connected():
        return {"status": "already_running"}
    _manager = LiveMarketDataManager(get_angel_session())
    try:
        _manager.start()
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"status": "started"}


@router.post("/stop")
def stop_live():
    global _manager
    if not _manager:
        return {"status": "not_running"}
    _manager.stop()
    return {"status": "stopped"}


@router.get("/status")
def live_status():
    if not _manager:
        return {"connected": False, "subscribed_tokens": 0, "tick_count": 0, "last_tick_at": None}
    return _manager.status()


@router.post("/subscribe")
def subscribe(req: SubscribeRequest):
    if not _manager:
        raise HTTPException(400, "Live data not started - call POST /live/start first")
    universe = [{"token": t} for t in req.tokens]
    try:
        _manager.subscribe(universe, mode=req.mode)
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"status": "subscribed", "tokens": req.tokens, "mode": req.mode}


@router.post("/unsubscribe")
def unsubscribe(req: SubscribeRequest):
    if not _manager:
        raise HTTPException(400, "Live data not started")
    universe = [{"token": t} for t in req.tokens]
    _manager.unsubscribe(universe, mode=req.mode)
    return {"status": "unsubscribed", "tokens": req.tokens, "mode": req.mode}


@router.get("/snapshots")
def snapshots(tokens: str):
    token_list = [t.strip() for t in tokens.split(",") if t.strip()]
    if not token_list:
        raise HTTPException(400, "tokens query param required, comma-separated")
    conn = get_raw_connection("local")
    cur = conn.cursor()
    cur.execute(
        """SELECT i.token, i.symbol, s.ltp, s.total_buy_qty, s.total_sell_qty, s.atp, s.open_interest, s.last_updated
           FROM market_snapshots_live s
           JOIN instruments i ON i.token = s.instrument_token
           WHERE i.token = ANY(%s)""",
        (token_list,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {
            "token": r[0], "symbol": r[1],
            "ltp": float(r[2]) if r[2] is not None else None,
            "total_buy_qty": r[3], "total_sell_qty": r[4],
            "atp": float(r[5]) if r[5] is not None else None,
            "open_interest": r[6],
            "last_updated": r[7].isoformat() if r[7] else None,
        }
        for r in rows
    ]
