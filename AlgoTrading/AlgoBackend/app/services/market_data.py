"""
Market data access layer over Angel One SmartAPI.

Built against the actual installed smartapi-python SDK (verified by
introspecting the package, not just copying notebook code) and Angel One's
documented rate limits:

    Bulk quote  (getMarketData)  : up to 50 symbols/request, 1 req/sec
    Historical  (getCandleData)  : 3 req/sec, 180/min, 5000/hour
    LTP         (ltpData)        : 10 req/sec  (kept for single-symbol use)

Note: the old notebook's live engine called `session.getQuote(...)`, which
does not exist on the installed SDK (confirmed via `dir(SmartConnect)`) --
it would have raised AttributeError the first time it ran live. This module
uses `getMarketData(mode="FULL", exchangeTokens=...)` instead, batched 50 at
a time, which is both correct and ~50x fewer HTTP calls than the old
per-symbol loop.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
import psycopg2.extras

from app.db.session import get_raw_connection
from app.services.angel_auth import AngelSessionPool

logger = logging.getLogger("market_data")

BULK_QUOTE_BATCH_SIZE = 50
BULK_QUOTE_MIN_INTERVAL_SEC = 1.05  # 1 req/sec limit, small margin
HISTORICAL_MIN_INTERVAL_SEC = 0.35  # 3 req/sec limit, small margin
CANDLE_CACHE_MIN_ROWS_FRACTION = 0.6  # trading days are ~5/7 of calendar days
CANDLE_CACHE_MAX_STALENESS_DAYS = 1   # Angel One's own data lags by up to 1 day anyway
RATE_LIMIT_COOLDOWN_SEC = 60  # circuit-breaker: pause this call type entirely after an "exceeding access rate" hit


@dataclass
class Quote:
    symbol: str
    token: str
    ltp: float
    open: float
    close: float  # previous day's close, per Angel One's FULL-mode field semantics
    volume: float

    @property
    def gap_percent(self) -> float:
        if not self.close:
            return 0.0
        return round((self.open - self.close) / self.close * 100, 3)


class MarketDataService:
    def __init__(self, pool: AngelSessionPool):
        self.pool = pool
        self._last_bulk_call = 0.0
        self._last_hist_call = 0.0
        self._bulk_rate_limited_until = 0.0
        self._hist_rate_limited_until = 0.0

    def _throttle(self, last_attr: str, min_interval: float):
        last = getattr(self, last_attr)
        wait = min_interval - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        setattr(self, last_attr, time.time())

    @staticmethod
    def _is_rate_limited_error(exc: Exception) -> bool:
        return "exceeding access rate" in str(exc).lower() or "access denied" in str(exc).lower()

    def bulk_quotes(self, instruments: list[dict], mode: str = "FULL") -> dict[str, Quote]:
        """
        instruments: [{"token": "...", "symbol": "..."}] for one exchange (NSE).
        Returns {symbol: Quote}. Batches into groups of 50 and respects the
        1 req/sec bulk-quote rate limit.
        """
        quotes: dict[str, Quote] = {}
        token_to_symbol = {i["token"]: i["symbol"] for i in instruments}
        tokens = list(token_to_symbol.keys())

        for i in range(0, len(tokens), BULK_QUOTE_BATCH_SIZE):
            batch = tokens[i:i + BULK_QUOTE_BATCH_SIZE]
            if time.time() < self._bulk_rate_limited_until:
                continue  # cooling down from a prior rate-limit hit, skip without calling out
            self._throttle("_last_bulk_call", BULK_QUOTE_MIN_INTERVAL_SEC)
            session = self.pool.get_session()
            try:
                resp = session.getMarketData(mode, {"NSE": batch})
            except Exception as e:
                if self._is_rate_limited_error(e):
                    logger.warning("Angel One bulk-quote rate limit hit -- backing off %ss", RATE_LIMIT_COOLDOWN_SEC)
                    self._bulk_rate_limited_until = time.time() + RATE_LIMIT_COOLDOWN_SEC
                else:
                    logger.exception("bulk quote call failed for batch starting at %s", i)
                continue
            if not resp or not resp.get("status"):
                logger.warning("bulk quote batch failed: %s", resp)
                continue
            for row in resp.get("data", {}).get("fetched", []):
                token = row.get("symbolToken") or row.get("symboltoken")
                symbol = token_to_symbol.get(token)
                if not symbol:
                    continue
                try:
                    quotes[symbol] = Quote(
                        symbol=symbol,
                        token=token,
                        ltp=float(row.get("ltp", 0) or 0),
                        open=float(row.get("open", 0) or 0),
                        close=float(row.get("close", 0) or 0),
                        volume=float(row.get("tradeVolume", row.get("volume", 0)) or 0),
                    )
                except (TypeError, ValueError):
                    continue
        return quotes

    def _load_cached_candles(self, token: str, as_of: date, lookback_days: int) -> Optional[pd.DataFrame]:
        """market_data_daily is a persistent local cache of completed daily
        candles -- Angel One's historical API has a hard 3 req/sec limit
        shared across everything this backend does with its one working key,
        so re-fetching the same symbol/day repeatedly (scanner restarts,
        repeated /market/indicators calls) is pure waste. Cache is trusted
        only if it's not stale by more than CANDLE_CACHE_MAX_STALENESS_DAYS --
        Angel One's own data lags by up to a day anyway, so this both allows
        same-day reuse and forces a refetch every new trading day."""
        conn = get_raw_connection("local")
        cur = conn.cursor()
        cur.execute(
            """SELECT date, open, high, low, close, volume FROM market_data_daily
               WHERE instrument_token = %s AND date BETWEEN %s AND %s
               ORDER BY date""",
            (token, as_of - timedelta(days=lookback_days), as_of),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        if not rows:
            return None
        most_recent = rows[-1][0]
        if most_recent < as_of - timedelta(days=CANDLE_CACHE_MAX_STALENESS_DAYS):
            return None
        if len(rows) < lookback_days * CANDLE_CACHE_MIN_ROWS_FRACTION:
            return None
        return pd.DataFrame(
            [(r[0].isoformat(), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows],
            columns=["time", "o", "h", "l", "c", "v"],
        )

    def _cache_candles(self, token: str, df: pd.DataFrame):
        rows = [
            (token, datetime.fromisoformat(r["time"]).date(), r["o"], r["h"], r["l"], r["c"], r["v"])
            for _, r in df.iterrows()
        ]
        try:
            conn = get_raw_connection("local")
            cur = conn.cursor()
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO market_data_daily (instrument_token, date, open, high, low, close, volume)
                   VALUES %s
                   ON CONFLICT (instrument_token, date) DO UPDATE SET
                       open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                       close = EXCLUDED.close, volume = EXCLUDED.volume""",
                rows,
            )
            conn.commit()
            cur.close()
            conn.close()
        except Exception:
            logger.exception("Failed to cache candles for token %s", token)

    def get_candles(self, token: str, as_of: date, lookback_days: int = 40) -> Optional[pd.DataFrame]:
        """Cached, throttled getCandleData call. Returns the full OHLCV frame
        (columns: time, o, h, l, c, v) up to and including `as_of`."""
        cached = self._load_cached_candles(token, as_of, lookback_days)
        if cached is not None:
            return cached

        if time.time() < self._hist_rate_limited_until:
            return None  # cooling down from a prior rate-limit hit, skip without calling out

        self._throttle("_last_hist_call", HISTORICAL_MIN_INTERVAL_SEC)
        session = self.pool.get_session()
        params = {
            "exchange": "NSE",
            "symboltoken": token,
            "interval": "ONE_DAY",
            "fromdate": (as_of - timedelta(days=lookback_days)).strftime("%Y-%m-%d 09:15"),
            "todate": as_of.strftime("%Y-%m-%d 15:30"),
        }
        try:
            res = session.getCandleData(params)
        except Exception as e:
            if self._is_rate_limited_error(e):
                logger.warning("Angel One historical-data rate limit hit -- backing off %ss", RATE_LIMIT_COOLDOWN_SEC)
                self._hist_rate_limited_until = time.time() + RATE_LIMIT_COOLDOWN_SEC
            else:
                logger.exception("getCandleData failed for token %s", token)
            return None
        if not res or not res.get("status") or not res.get("data"):
            return None
        # NOTE: deliberately NOT using pd.to_datetime here -- it segfaults
        # (Windows access violation, reproduced twice) inside pandas 2.2.2's
        # strptime-fallback path on this Python 3.14 + source-built-pandas
        # combination (no prebuilt wheel exists yet for cp314). Plain
        # datetime.fromisoformat sidesteps that C path entirely; the "time"
        # column stays as ISO strings (object dtype), which is all callers
        # here need (daily_baseline/indicators never require a Timestamp).
        rows = [r for r in res["data"] if datetime.fromisoformat(r[0]).date() <= as_of]
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["time", "o", "h", "l", "c", "v"])
        self._cache_candles(token, df)
        return df

    def daily_baseline(self, token: str, as_of: date, lookback_days: int = 40) -> Optional[dict]:
        """20-day avg volume + prev close, from daily candles ending at `as_of`."""
        df = self.get_candles(token, as_of, lookback_days)
        if df is None or len(df) < 20:
            return None
        return {"avg_vol": float(df["v"].tail(20).mean()), "prev_close": float(df.iloc[-1]["c"])}
