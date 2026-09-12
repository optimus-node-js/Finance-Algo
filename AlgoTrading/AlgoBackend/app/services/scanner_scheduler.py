"""
Orchestrates the live scan -> signal -> paper-trade -> square-off cycle as a
background job inside the FastAPI process (APScheduler), so running the
backend IS running the trading engine -- no separate script to babysit.

Lifecycle, controlled via app/routers/trading.py:
    POST /trading/scanner/start   -> kicks off login + baseline loading in a
                                      background thread and returns immediately.
                                      Baseline loading itself takes minutes for
                                      500 stocks (historical-data rate limit),
                                      so it must never block the request/other
                                      endpoints -- confirmed live that it did
                                      before this fix (a plain GET /scanner/status
                                      queued behind it for 15+ minutes).
    POST /trading/scanner/stop    -> aborts an in-flight baseline load (if any)
                                      or cancels the recurring job, squares off
                                      any open paper positions immediately.
    GET  /trading/scanner/status  -> what's running/loading, open positions,
                                      trade count, live-data coverage.

The recurring job itself does one of three things each tick:
    1. before SQUARE_OFF_TIME and under MAX_TRADES_PER_DAY: get quotes for the
       whole universe (live WebSocket ticks if warmed up, REST bulk-fetch as a
       fallback), run the active strategy, paper-enter any new signals.
    2. at/after SQUARE_OFF_TIME (once): fetch exit quotes for open positions,
       square off, write the day's portfolio_ledger row.
    3. market closed: no-op.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import settings
from app.db.session import get_raw_connection
from app.services.angel_auth import AngelSessionPool, load_credentials_from_env
from app.services.market_data import MarketDataService, Quote
from app.services.market_ws import LiveMarketDataManager
from app.services.paper_trading import PaperTradingEngine
from app.services.strategy_engine import STRATEGIES

logger = logging.getLogger("scanner")

LIVE_DATA_COVERAGE_THRESHOLD = 0.8  # fraction of universe needing a live tick before we trust it over REST


def is_market_open() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return "09:15" <= now.strftime("%H:%M") <= "15:30"


class ScannerService:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.pool: AngelSessionPool | None = None
        self.market_data: MarketDataService | None = None
        self.live_manager: LiveMarketDataManager | None = None
        self.engine: PaperTradingEngine | None = None
        self.strategy_name = "GapVolShock_v1"
        self.universe: list[dict] = []
        self.baselines: dict[str, dict] = {}
        self.trades_taken_today = 0
        self.squared_off_today = False

        self._state = "idle"  # idle | loading_baselines | running | failed
        self._baseline_progress = {"done": 0, "total": 0}
        self._abort_requested = threading.Event()
        self._last_error: str | None = None
        self._start_lock = threading.Lock()

    def _load_universe(self, conn) -> list[dict]:
        cur = conn.cursor()
        cur.execute("SELECT token, symbol FROM instruments WHERE is_active = TRUE")
        rows = [{"token": t, "symbol": s} for t, s in cur.fetchall()]
        cur.close()
        return rows

    def request_start(self) -> tuple[dict, bool]:
        """Guards + flips state for a new start. Returns (response, should_load)
        -- the caller (router) schedules _start_blocking via FastAPI's
        BackgroundTasks when should_load is True. Deliberately NOT a raw
        threading.Thread: pandas' datetime C extension (used in get_candles)
        segfaulted on Windows the one time this ran on a brand-new OS thread
        here (Python 3.14 + a source-built pandas 2.2.2 -- no prebuilt wheel
        exists yet). Routing through BackgroundTasks reuses FastAPI's already-
        proven-stable anyio worker-thread pool instead."""
        with self._start_lock:
            if self._state in ("loading_baselines", "running"):
                return {"status": self._state}, False
            self._state = "loading_baselines"
            self._abort_requested.clear()
            self._last_error = None
            self._baseline_progress = {"done": 0, "total": 0}

        return {
            "status": "starting",
            "message": "Login + baseline fetch running in background; poll GET /trading/scanner/status",
        }, True

    def _start_blocking(self):
        try:
            conn = get_raw_connection("local")
            self.universe = self._load_universe(conn)
            if not self.universe:
                conn.close()
                raise RuntimeError("instruments table is empty - run scripts/seed_instruments.py first")

            self.pool = AngelSessionPool(load_credentials_from_env())
            self.pool.login_all()
            self.market_data = MarketDataService(self.pool)
            self.engine = PaperTradingEngine(conn, capital_per_trade=settings.CAPITAL_PER_TRADE)

            logger.info("Fetching baselines for %d instruments (one-time, respects rate limits)...", len(self.universe))
            today = datetime.now().date()
            self.baselines = {}
            self._baseline_progress["total"] = len(self.universe)
            for inst in self.universe:
                if self._abort_requested.is_set():
                    logger.info("Baseline fetch aborted after %d/%d instruments.",
                                self._baseline_progress["done"], len(self.universe))
                    self._state = "idle"
                    return
                base = self.market_data.daily_baseline(inst["token"], today)
                if base:
                    self.baselines[inst["symbol"]] = base
                self._baseline_progress["done"] += 1
            logger.info("Baselines ready for %d/%d instruments.", len(self.baselines), len(self.universe))

            self.live_manager = LiveMarketDataManager(self.pool.get_session())
            try:
                self.live_manager.start()
                self.live_manager.subscribe(self.universe, mode="QUOTE")
            except Exception:
                logger.exception("Live WebSocket manager failed to start; scanner will use REST polling only.")
                self.live_manager = None

            self.trades_taken_today = 0
            self.squared_off_today = False
            self.scheduler.add_job(self._tick, "interval", seconds=settings.SCAN_INTERVAL_SEC, id="scan_tick",
                                    replace_existing=True)
            if not self.scheduler.running:
                self.scheduler.start()
            self._state = "running"
        except Exception as e:
            logger.exception("Scanner start failed")
            self._last_error = str(e)
            self._state = "failed"

    def stop(self):
        self._abort_requested.set()
        if self.scheduler.get_job("scan_tick"):
            self.scheduler.remove_job("scan_tick")
        if self.live_manager:
            self.live_manager.stop()
            self.live_manager = None
        if self.engine and self.engine.open_positions:
            self._square_off_now()
        self._state = "idle"
        return {"status": "stopped"}

    def status(self) -> dict:
        coverage = 0.0
        if self.live_manager and self.universe:
            coverage = len(self.live_manager.get_latest_quotes(self.universe)) / len(self.universe)
        return {
            "state": self._state,
            "running": self._state == "running",
            "baseline_progress": self._baseline_progress,
            "last_error": self._last_error,
            "market_open": is_market_open(),
            "strategy": self.strategy_name,
            "trades_taken_today": self.trades_taken_today,
            "max_trades_per_day": settings.MAX_TRADES_PER_DAY,
            "open_positions": list(self.engine.open_positions.keys()) if self.engine else [],
            "squared_off_today": self.squared_off_today,
            "using_live_data": bool(self.live_manager and self.live_manager.is_connected()
                                     and coverage >= LIVE_DATA_COVERAGE_THRESHOLD),
            "live_tick_coverage": round(coverage, 3),
        }

    def _get_quotes(self) -> dict[str, Quote]:
        """Live WebSocket ticks when warmed up, REST bulk-fetch as a safety-net
        fallback -- a dropped socket or a still-warming-up subscription must
        not mean the scanner silently stops generating signals."""
        if self.live_manager and self.live_manager.is_connected():
            quotes = self.live_manager.get_latest_quotes(self.universe)
            if len(quotes) >= LIVE_DATA_COVERAGE_THRESHOLD * len(self.universe):
                return quotes
        return self.market_data.bulk_quotes(self.universe, mode="FULL")

    def _square_off_now(self):
        symbols = list(self.engine.open_positions.keys())
        if not symbols:
            return
        universe_lookup = [{"token": p.token, "symbol": s} for s, p in self.engine.open_positions.items()]
        quotes = self.market_data.bulk_quotes(universe_lookup, mode="LTP")
        exit_prices = {s: q.ltp for s, q in quotes.items()}
        result = self.engine.square_off_all(exit_prices)
        logger.info("Square-off result: %s", result)
        self.squared_off_today = True

    def _tick(self):
        try:
            if not is_market_open():
                return

            now_str = datetime.now().strftime("%H:%M")

            if now_str >= settings.SQUARE_OFF_TIME and not self.squared_off_today:
                self._square_off_now()
                return

            if self.trades_taken_today >= settings.MAX_TRADES_PER_DAY or now_str >= settings.SQUARE_OFF_TIME:
                return

            quotes = self._get_quotes()
            strategy = STRATEGIES[self.strategy_name]
            signals = strategy.scan(quotes, self.baselines)

            for signal in signals:
                if self.trades_taken_today >= settings.MAX_TRADES_PER_DAY:
                    break
                pos = self.engine.enter(signal)
                if pos:
                    self.trades_taken_today += 1
        except Exception:
            logger.exception("Scan tick failed")


scanner_service = ScannerService()
