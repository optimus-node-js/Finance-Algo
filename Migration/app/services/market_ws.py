"""
Live market data over Angel One's SmartWebSocketV2 (binary tick stream).

Built against the actual installed smartapi-python SDK (verified via
inspect.getsource on SmartWebSocketV2.__init__/connect/subscribe/_parse_binary_data,
not just docs). Ticks arrive as parsed dicts via the on_data callback; QUOTE
mode carries last_traded_price, open/high/low/closed_price, and volume, all
prices in paise (divide by 100). connect() is a blocking run_forever() call,
so it always runs in its own daemon thread.

Per-tick Postgres writes would be disproportionate -- a busy universe can
produce many ticks/sec per token. Instead the latest tick per token is kept
in memory and flushed to market_snapshots_live on a short interval.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Optional

import psycopg2.extras
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

from app.db.session import get_raw_connection
from app.services.market_data import Quote

logger = logging.getLogger("market_ws")

FLUSH_INTERVAL_SEC = 2.0
CONNECT_TIMEOUT_SEC = 15
TICK_STALE_THRESHOLD_SEC = 90  # no ticks at all in this long -> treat as unhealthy, fall back to REST

_MODE_MAP = {"LTP": SmartWebSocketV2.LTP_MODE, "QUOTE": SmartWebSocketV2.QUOTE, "SNAP_QUOTE": SmartWebSocketV2.SNAP_QUOTE}


class LiveMarketDataManager:
    """Wraps one SmartWebSocketV2 connection. One instance is enough for the
    whole universe -- all 5 rotation keys share the same underlying trading
    account, so streaming doesn't need key rotation (that's only for REST
    rate limits)."""

    def __init__(self, session):
        self.ws = SmartWebSocketV2(
            auth_token=session.access_token,
            api_key=session.api_key,
            client_code=session.userId,
            feed_token=session.feed_token,
        )
        self.ws.on_data = self._on_data
        self.ws.on_open = self._on_open
        self.ws.on_error = self._on_error
        self.ws.on_close = self._on_close
        # SmartWebSocketV2's own internal _on_close(self, wsapp) doesn't
        # accept the extra (close_status_code, close_msg) args websocket-client
        # actually passes on close -- confirmed via inspect.getsource, it
        # raises TypeError every time a close event fires, which means the
        # user-facing self.ws.on_close hook above never runs and _connected
        # never clears. Patched at the instance level (post-construction, before
        # connect() ever reads it) so our own close handling actually executes.
        self.ws._on_close = lambda wsapp, *args: self.ws.on_close(wsapp)

        self._connected = threading.Event()
        self._ticks: dict[str, dict] = {}
        self._tick_lock = threading.Lock()
        self._tick_count = 0
        self._last_tick_at: Optional[datetime] = None
        self._stop_flush = threading.Event()

    # -- lifecycle --

    def start(self):
        threading.Thread(target=self.ws.connect, daemon=True, name="angel-ws").start()
        if not self._connected.wait(timeout=CONNECT_TIMEOUT_SEC):
            raise RuntimeError(f"Angel One WebSocket did not open within {CONNECT_TIMEOUT_SEC}s")
        self._stop_flush.clear()
        threading.Thread(target=self._flush_loop, daemon=True, name="angel-ws-flush").start()

    def stop(self):
        self._stop_flush.set()
        self.ws.close_connection()
        self._connected.clear()

    def is_connected(self) -> bool:
        """True only if the socket is marked open AND (no tick expected yet,
        or the last one wasn't too long ago). A clean on_close event clears
        _connected immediately; this staleness check is the second, independent
        signal for the failure modes a close event doesn't cover -- a half-open
        TCP connection or silent network drop that never sends a close frame."""
        if not self._connected.is_set():
            return False
        if self._last_tick_at is None:
            return True
        return (datetime.now() - self._last_tick_at).total_seconds() < TICK_STALE_THRESHOLD_SEC

    # -- subscription --

    def subscribe(self, universe: list[dict], mode: str = "QUOTE", exchange_type: int = SmartWebSocketV2.NSE_CM):
        if not self._connected.wait(timeout=CONNECT_TIMEOUT_SEC):
            raise RuntimeError("Cannot subscribe -- WebSocket never connected")
        tokens = [inst["token"] for inst in universe]
        self.ws.subscribe("algo1", _MODE_MAP[mode.upper()], [{"exchangeType": exchange_type, "tokens": tokens}])

    def unsubscribe(self, universe: list[dict], mode: str = "QUOTE", exchange_type: int = SmartWebSocketV2.NSE_CM):
        tokens = [inst["token"] for inst in universe]
        self.ws.unsubscribe("algo1", _MODE_MAP[mode.upper()], [{"exchangeType": exchange_type, "tokens": tokens}])

    # -- callbacks (run on the websocket thread) --

    def _on_open(self, wsapp):
        logger.info("Angel One WebSocket connected")
        self._connected.set()

    def _on_error(self, wsapp, error):
        logger.error("Angel One WebSocket error: %s", error)

    def _on_close(self, wsapp, *args):
        logger.info("Angel One WebSocket closed")
        self._connected.clear()

    def _on_data(self, wsapp, tick: dict):
        token = tick.get("token")
        if not token:
            return
        with self._tick_lock:
            self._ticks[token] = tick
            self._tick_count += 1
            self._last_tick_at = datetime.now()

    # -- reads --

    def get_latest_quotes(self, universe: list[dict]) -> dict[str, Quote]:
        """Quote per token with a cached QUOTE/SNAP_QUOTE tick (LTP-mode ticks
        only carry last_traded_price, not open/volume, so they're skipped)."""
        with self._tick_lock:
            ticks_snapshot = dict(self._ticks)
        quotes: dict[str, Quote] = {}
        for inst in universe:
            tick = ticks_snapshot.get(inst["token"])
            if not tick or "open_price_of_the_day" not in tick:
                continue
            quotes[inst["symbol"]] = Quote(
                symbol=inst["symbol"],
                token=inst["token"],
                ltp=tick["last_traded_price"] / 100,
                open=tick["open_price_of_the_day"] / 100,
                close=tick["closed_price"] / 100,
                volume=float(tick.get("volume_trade_for_the_day", 0)),
            )
        return quotes

    def status(self) -> dict:
        subscribed = sum(len(tokens) for mode_dict in self.ws.input_request_dict.values() for tokens in mode_dict.values())
        return {
            "connected": self.is_connected(),
            "subscribed_tokens": subscribed,
            "tick_count": self._tick_count,
            "last_tick_at": self._last_tick_at.isoformat() if self._last_tick_at else None,
        }

    # -- persistence --

    def _flush_loop(self):
        while not self._stop_flush.wait(FLUSH_INTERVAL_SEC):
            self._flush_to_db()

    def _flush_to_db(self):
        with self._tick_lock:
            ticks_snapshot = list(self._ticks.items())
        if not ticks_snapshot:
            return
        rows = [
            (
                token,
                tick.get("last_traded_price", 0) / 100,
                int(tick["total_buy_quantity"]) if tick.get("total_buy_quantity") is not None else None,
                int(tick["total_sell_quantity"]) if tick.get("total_sell_quantity") is not None else None,
                tick.get("average_traded_price", 0) / 100 if tick.get("average_traded_price") else None,
                tick.get("open_interest"),
            )
            for token, tick in ticks_snapshot
        ]
        try:
            conn = get_raw_connection("local")
            cur = conn.cursor()
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO market_snapshots_live
                       (instrument_token, ltp, total_buy_qty, total_sell_qty, atp, open_interest, last_updated)
                   VALUES %s
                   ON CONFLICT (instrument_token) DO UPDATE SET
                       ltp = EXCLUDED.ltp,
                       total_buy_qty = EXCLUDED.total_buy_qty,
                       total_sell_qty = EXCLUDED.total_sell_qty,
                       atp = EXCLUDED.atp,
                       open_interest = EXCLUDED.open_interest,
                       last_updated = CURRENT_TIMESTAMP""",
                rows,
                template="(%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)",
            )
            conn.commit()
            cur.close()
            conn.close()
        except Exception:
            logger.exception("Failed to flush live ticks to market_snapshots_live")
