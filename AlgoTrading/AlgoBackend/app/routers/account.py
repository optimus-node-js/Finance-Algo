from fastapi import APIRouter, HTTPException

from app.services.angel_auth import get_angel_session

router = APIRouter(prefix="/account", tags=["Account"])

# Read-only visibility into the real Angel One account. Deliberately excludes
# placeOrder/modifyOrder/cancelOrder/gttCreateRule/gttModifyRule/gttCancelRule
# and anything else that could touch real money or standing orders -- that
# stays out of scope per the project's paper-trading safety rules.


@router.get("/profile")
def profile():
    session = get_angel_session()
    try:
        return session.getProfile(session.refresh_token)
    except Exception as e:
        raise HTTPException(502, f"getProfile call failed: {e}")


@router.get("/funds")
def funds():
    session = get_angel_session()
    try:
        return session.rmsLimit()
    except Exception as e:
        raise HTTPException(502, f"rmsLimit call failed: {e}")


@router.get("/holdings")
def holdings():
    session = get_angel_session()
    try:
        return session.holding()
    except Exception as e:
        raise HTTPException(502, f"holding call failed: {e}")


@router.get("/holdings/all")
def holdings_all():
    session = get_angel_session()
    try:
        return session.allholding()
    except Exception as e:
        raise HTTPException(502, f"allholding call failed: {e}")


@router.get("/positions")
def positions():
    session = get_angel_session()
    try:
        return session.position()
    except Exception as e:
        raise HTTPException(502, f"position call failed: {e}")


@router.get("/orders")
def orders():
    session = get_angel_session()
    try:
        return session.orderBook()
    except Exception as e:
        raise HTTPException(502, f"orderBook call failed: {e}")


@router.get("/trades")
def trades():
    session = get_angel_session()
    try:
        return session.tradeBook()
    except Exception as e:
        raise HTTPException(502, f"tradeBook call failed: {e}")


@router.get("/gtt")
def gtt_rules(status: str = "FORALL"):
    """Read-only GTT rule listing (no rule creation/modification exposed)."""
    session = get_angel_session()
    try:
        return session.gttLists([status], 1, 100)
    except Exception as e:
        raise HTTPException(502, f"gttLists call failed: {e}")
