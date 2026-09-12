from datetime import date

from fastapi import APIRouter, HTTPException

from app.db.session import get_raw_connection
from app.services.angel_auth import AngelSessionPool, angel_pool, get_angel_session, load_credentials_from_env
from app.services.indicators import compute_indicators
from app.services.market_data import MarketDataService
from app.services.strategy_engine import rank_momentum

router = APIRouter(prefix="/market", tags=["Market Data"])


def _shared_market_data() -> MarketDataService:
    """Reuses the module-level angel_auth singleton pool (logs in once per
    process) instead of AngelSessionPool(...).login_all() per request."""
    get_angel_session()
    return MarketDataService(angel_pool)


@router.get("/instruments/count")
def instrument_count():
    conn = get_raw_connection("local")
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM instruments WHERE is_active = TRUE")
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return {"active_instruments": count}


@router.get("/instruments/search")
def search_instruments(
    q: str = None,
    exchange: str = None,
    is_active: bool = True,
    is_cash_with_fno: bool = None,
    sector: str = None,
    market_cap_category: str = None,
    index: str = None,
    limit: int = 50,
    offset: int = 0,
):
    """
    Flexible equity search/filter. q/exchange/is_active/is_cash_with_fno are
    reliably populated today (seeded from nifty500.json). sector/
    market_cap_category/index are forward-compatible: instruments.sector_id,
    market_cap_category and index_constituents are currently empty (that
    enrichment pipeline -- POST /seed/stocks/full-refresh -- hasn't been run
    with a fresh ANGEL_COOKIE) so these simply return an empty list rather
    than erroring, and start working the day that data exists.
    """
    where = ["i.is_active = %s"]
    params: list = [is_active]

    if q:
        where.append("(i.symbol ILIKE %s OR i.trading_symbol ILIKE %s OR i.company_name ILIKE %s)")
        like = f"%{q}%"
        params.extend([like, like, like])
    if exchange:
        where.append("ex.code = %s")
        params.append(exchange.upper())
    if is_cash_with_fno is not None:
        where.append("i.is_cash_with_fno = %s")
        params.append(is_cash_with_fno)
    if sector:
        where.append("sec.name ILIKE %s")
        params.append(sector)
    if market_cap_category:
        where.append("i.market_cap_category ILIKE %s")
        params.append(market_cap_category)
    if index:
        where.append("""i.token IN (
            SELECT ic.instrument_token FROM index_constituents ic
            JOIN indices idx ON idx.id = ic.index_id WHERE idx.name ILIKE %s
        )""")
        params.append(index)

    query = f"""
        SELECT i.token, i.symbol, i.trading_symbol, i.company_name, ex.code,
               sec.name, i.market_cap_category, i.is_cash_with_fno, i.is_active
        FROM instruments i
        LEFT JOIN exchanges ex ON ex.id = i.exchange_id
        LEFT JOIN sectors sec ON sec.id = i.sector_id
        WHERE {' AND '.join(where)}
        ORDER BY i.symbol
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])

    conn = get_raw_connection("local")
    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [
        {
            "token": r[0], "symbol": r[1], "trading_symbol": r[2], "company_name": r[3],
            "exchange": r[4], "sector": r[5], "market_cap_category": r[6],
            "is_cash_with_fno": r[7], "is_active": r[8],
        }
        for r in rows
    ]


@router.get("/indicators/{symbol}")
def indicators_for_symbol(symbol: str, lookback_days: int = 60):
    """On-demand technical indicators (SMA/EMA/RSI/MACD/Bollinger/ATR/VWAP)
    computed from historical daily candles -- Angel One doesn't precompute
    these itself, so we compute them here from raw OHLCV."""
    conn = get_raw_connection("local")
    cur = conn.cursor()
    cur.execute(
        "SELECT token FROM instruments WHERE symbol = %s OR trading_symbol = %s LIMIT 1",
        (symbol, symbol),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        raise HTTPException(404, f"Symbol '{symbol}' not found in instruments")
    token = row[0]

    md = _shared_market_data()
    df = md.get_candles(token, date.today(), lookback_days=lookback_days)
    if df is None or len(df) < 20:
        raise HTTPException(404, f"Not enough candle history for '{symbol}' to compute indicators")

    return {"symbol": symbol, "token": token, "as_of": df.iloc[-1]["time"][:10],
            "candles_used": len(df), **compute_indicators(df)}


@router.get("/momentum")
def momentum(top_n: int = 10, limit_universe: int = 200):
    """
    Ad-hoc snapshot: bulk-fetches quotes for up to `limit_universe` active
    instruments and ranks by %-change-from-prev-close. Answers "what's got
    the highest/lowest momentum right now" on demand -- does NOT require the
    scanner to be running.
    """
    conn = get_raw_connection("local")
    cur = conn.cursor()
    cur.execute("SELECT token, symbol FROM instruments WHERE is_active = TRUE LIMIT %s", (limit_universe,))
    universe = [{"token": t, "symbol": s} for t, s in cur.fetchall()]
    cur.close()
    conn.close()

    if not universe:
        raise HTTPException(404, "No instruments found - run scripts/seed_instruments.py first")

    pool = AngelSessionPool(load_credentials_from_env())
    pool.login_all()
    md = MarketDataService(pool)
    quotes = md.bulk_quotes(universe, mode="FULL")
    return rank_momentum(quotes, top_n=top_n)


@router.get("/gainers-losers")
def gainers_losers(datatype: str = "PercPriceGainers", expirytype: str = "NEAR"):
    """Angel One's own precomputed top gainers/losers -- a real broker-side
    market-intelligence endpoint, distinct from indicators (which we compute
    ourselves; Angel One does not compute RSI/MACD)."""
    session = get_angel_session()
    try:
        return session.gainersLosers({"datatype": datatype, "expirytype": expirytype})
    except Exception as e:
        raise HTTPException(502, f"gainersLosers call failed: {e}")


@router.get("/oi-buildup")
def oi_buildup(datatype: str = "Long Built Up", expirytype: str = "NEAR"):
    """F&O open-interest buildup categorization (long/short buildup, long
    unwinding, short covering) -- broker-precomputed, another real signal."""
    session = get_angel_session()
    try:
        return session.oIBuildup({"datatype": datatype, "expirytype": expirytype})
    except Exception as e:
        raise HTTPException(502, f"oIBuildup call failed: {e}")


@router.get("/put-call-ratio")
def put_call_ratio():
    session = get_angel_session()
    try:
        return session.putCallRatio()
    except Exception as e:
        raise HTTPException(502, f"putCallRatio call failed: {e}")


@router.get("/ltp")
def ltp(exchange: str, symbol: str, token: str):
    """Lighter single-symbol quote (10 req/sec limit vs bulk_quotes' 1 req/sec)
    -- for pinpointing one stock once /market/instruments/search finds it."""
    session = get_angel_session()
    try:
        return session.ltpData(exchange, symbol, token)
    except Exception as e:
        raise HTTPException(502, f"ltpData call failed: {e}")


@router.get("/search-scrip")
def search_scrip(exchange: str, query: str):
    """Live symbol/token lookup directly against Angel One's own master --
    complements /market/instruments/search for symbols not yet seeded locally."""
    session = get_angel_session()
    try:
        return session.searchScrip(exchange, query)
    except Exception as e:
        raise HTTPException(502, f"searchScrip call failed: {e}")
