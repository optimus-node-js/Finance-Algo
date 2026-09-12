"""
Paper trading engine: position sizing, simulated fills, tax calc, and all
Postgres persistence (trade_signals -> orders -> trades -> portfolio_ledger).

Position sizing is FIXED capital per trade (CAPITAL_PER_TRADE), not a
compounding balance -- see project memory / walk-forward validation notes
for why the notebook's compounding-at-5x-leverage simulator produced a
misleading +23,346% backtest number. This engine intentionally does not
implement leverage or compounding.

Every write here sets is_paper = TRUE / mode = 'PAPER'. There is no flag
anywhere in this module that places a real order -- going live would mean
writing a new, separate, explicitly-reviewed code path, not flipping a
setting here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import psycopg2.extras
from psycopg2.extensions import connection as PGConnection

from app.services.strategy_engine import Signal

logger = logging.getLogger("paper_trading")


def calculate_taxes(buy_val: float, sell_val: float) -> tuple[float, dict]:
    """Angel One intraday-equity charges (brokerage/STT/exchange/SEBI/stamp/GST)."""
    brk_buy = min(20, 0.0003 * buy_val)
    brk_sell = min(20, 0.0003 * sell_val)
    brokerage = brk_buy + brk_sell
    stt = 0.00025 * sell_val
    etc = 0.0000297 * (buy_val + sell_val)
    sebi = 0.000001 * (buy_val + sell_val)
    stamp = 0.00003 * buy_val
    gst = 0.18 * (brokerage + etc + sebi)
    breakdown = {"brokerage": brokerage, "stt": stt, "transaction_charges": etc,
                 "sebi_fees": sebi, "stamp_duty": stamp, "gst": gst}
    return sum(breakdown.values()), breakdown


@dataclass
class OpenPosition:
    symbol: str
    token: str
    qty: int
    entry_price: float
    order_id: int
    opened_at: datetime = field(default_factory=datetime.now)


class PaperTradingEngine:
    def __init__(self, conn: PGConnection, capital_per_trade: float = 20_000):
        self.conn = conn
        self.capital_per_trade = capital_per_trade
        self.open_positions: dict[str, OpenPosition] = {}

    def enter(self, signal: Signal) -> OpenPosition | None:
        """Fixed-capital paper buy. Records signal -> order -> trade."""
        if signal.symbol in self.open_positions:
            return None
        qty = int(self.capital_per_trade / signal.price) if signal.price else 0
        if qty < 1:
            return None

        cur = self.conn.cursor()
        cur.execute(
            """INSERT INTO trade_signals (instrument_token, strategy_name, signal_price, technical_data, is_processed)
               VALUES (%s, %s, %s, %s, TRUE) RETURNING id""",
            (signal.token, signal.strategy_name, signal.price, psycopg2.extras.Json(signal.technical_data)),
        )
        signal_id = cur.fetchone()[0]

        cur.execute(
            """INSERT INTO orders (signal_id, instrument_token, is_paper, transaction_type, order_type,
                                    product_type, quantity, status, status_message)
               VALUES (%s, %s, TRUE, 'BUY', 'MARKET', 'INTRADAY', %s, 'EXECUTED', 'Paper fill at LTP')
               RETURNING order_id""",
            (signal_id, signal.token, qty),
        )
        order_id = cur.fetchone()[0]

        cur.execute(
            """INSERT INTO trades (order_id, instrument_token, execution_price, execution_time, quantity_filled)
               VALUES (%s, %s, %s, CURRENT_TIMESTAMP, %s)""",
            (order_id, signal.token, signal.price, qty),
        )
        self.conn.commit()
        cur.close()

        pos = OpenPosition(symbol=signal.symbol, token=signal.token, qty=qty,
                            entry_price=signal.price, order_id=order_id)
        self.open_positions[signal.symbol] = pos
        logger.info("PAPER BUY %s qty=%s @ %.2f (%s)", signal.symbol, qty, signal.price, signal.technical_data)
        return pos

    def square_off_all(self, exit_prices: dict[str, float]) -> dict:
        """exit_prices: {symbol: ltp}. Closes every open position, records
        taxes/slippage on the trade row, and upserts today's PAPER ledger row."""
        if not self.open_positions:
            return {"trades_closed": 0}

        cur = self.conn.cursor()
        total_gross = total_tax = 0.0
        wins = 0
        closed = 0
        for symbol, pos in list(self.open_positions.items()):
            exit_price = exit_prices.get(symbol, pos.entry_price)
            buy_val, sell_val = pos.qty * pos.entry_price, pos.qty * exit_price
            gross = sell_val - buy_val
            taxes, breakdown = calculate_taxes(buy_val, sell_val)
            net = gross - taxes
            total_gross += gross
            total_tax += taxes
            wins += 1 if net > 0 else 0
            closed += 1

            cur.execute(
                """UPDATE trades SET slippage = %s, brokerage=%s, stt=%s, transaction_charges=%s,
                                      sebi_fees=%s, stamp_duty=%s, gst=%s
                   WHERE order_id = %s""",
                (exit_price - pos.entry_price, breakdown["brokerage"], breakdown["stt"],
                 breakdown["transaction_charges"], breakdown["sebi_fees"], breakdown["stamp_duty"],
                 breakdown["gst"], pos.order_id),
            )
            logger.info("SQUARE-OFF %s exit=%.2f net_pnl=%.2f", symbol, exit_price, net)
            del self.open_positions[symbol]

        net_total = total_gross - total_tax
        today = datetime.now().date()
        cur.execute(
            """INSERT INTO portfolio_ledger (date, mode, opening_balance, closing_balance,
                                              total_gross_pnl, total_charges_paid, net_pnl,
                                              trades_count, win_rate)
               VALUES (%s, 'PAPER', 0, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (date, mode) DO UPDATE SET
                 total_gross_pnl = portfolio_ledger.total_gross_pnl + EXCLUDED.total_gross_pnl,
                 total_charges_paid = portfolio_ledger.total_charges_paid + EXCLUDED.total_charges_paid,
                 net_pnl = portfolio_ledger.net_pnl + EXCLUDED.net_pnl,
                 closing_balance = portfolio_ledger.closing_balance + EXCLUDED.net_pnl,
                 trades_count = portfolio_ledger.trades_count + EXCLUDED.trades_count""",
            (today, net_total, total_gross, total_tax, net_total, closed,
             round(100 * wins / closed, 2) if closed else 0),
        )
        self.conn.commit()
        cur.close()
        return {"trades_closed": closed, "net_pnl": net_total}
