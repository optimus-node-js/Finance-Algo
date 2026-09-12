"""
Live Gap+VolShock paper-trading scanner — run this LOCALLY (needs real
internet to reach Angel One; the cloud sandbox can't).

What it does, every scan cycle during market hours (9:15-15:30 IST):
  1. Pulls the instrument universe from your local `instruments` table
     (run scripts/seed_instruments.py first if you haven't).
  2. Once per run: fetches each stock's 20-day avg volume + prev close
     (baseline) via getCandleData.
  3. Loops quotes every SCAN_INTERVAL_SEC seconds, checks Gap% > MIN_GAP_PERCENT
     and Volume > VOL_SHOCK_MULT * avg_vol.
  4. On a signal: sizes the position with FIXED capital (CAPITAL_PER_TRADE),
     NOT compounding balance — this avoids the leverage-compounding trap that
     inflated the old backtest to +23,346% ROI. Writes trade_signals -> orders
     -> trades rows, is_paper always TRUE.
  5. At/after SQUARE_OFF_TIME, closes any still-open paper positions at the
     current LTP, computes realistic taxes (same formula as your notebook's
     tax engine), and upserts today's portfolio_ledger row (mode='PAPER').

Safety: PAPER_MODE cannot be turned off by editing this file. To ever place a
REAL order you'd need to explicitly change place_order() yourself — there is
no env var or flag that flips it, on purpose.

Setup (one time):
    createdb algotrading          # or whatever LOCAL_DB_NAME you use
    psql -U postgres -d algotrading -f Migration/sql/schema.sql
    python Migration/scripts/seed_instruments.py Trading/Stocks/data/nifty500.json
    pip install -r Migration/requirements.txt

Run:
    python Migration/scripts/run_paper_scanner.py
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta

import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # so `app.services...` imports work
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from app.services.angel_auth import AngelSessionPool, load_credentials_from_env  # noqa: E402

# ==========================================
# MISSION CONTROL
# ==========================================
CAPITAL_PER_TRADE = 20_000   # FIXED per trade, does not compound
MAX_TRADES_PER_DAY = 3
MIN_GAP_PERCENT = 1.5
VOL_SHOCK_MULT = 3.0
SCAN_INTERVAL_SEC = 15
SQUARE_OFF_TIME = "15:15"
STRATEGY_NAME = "GapVolShock_v1_paper"

DB_HOST = os.environ.get("LOCAL_DB_HOST", "localhost")
DB_PORT = os.environ.get("LOCAL_DB_PORT", "5432")
DB_USER = os.environ.get("LOCAL_DB_USER", "postgres")
DB_PASS = os.environ.get("LOCAL_DB_PASS", "postgres")
DB_NAME = os.environ.get("LOCAL_DB_NAME", "algotrading")


def db_conn():
    return psycopg2.connect(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS, dbname=DB_NAME)


def calculate_taxes(buy_val: float, sell_val: float) -> float:
    """Same intraday-equity tax formula as the notebook's portfolio simulator."""
    brk_buy = min(20, 0.0003 * buy_val)
    brk_sell = min(20, 0.0003 * sell_val)
    brokerage = brk_buy + brk_sell
    stt = 0.00025 * sell_val
    etc = 0.0000297 * (buy_val + sell_val)
    sebi = 0.000001 * (buy_val + sell_val)
    stamp = 0.00003 * buy_val
    gst = 0.18 * (brokerage + etc + sebi)
    return brokerage + stt + etc + sebi + stamp + gst, {
        "brokerage": brokerage, "stt": stt, "etc": etc, "sebi": sebi, "stamp": stamp, "gst": gst,
    }


def is_market_open() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return "09:15" <= now.strftime("%H:%M") <= "15:30"


def get_last_trading_day() -> "datetime.date":
    today = datetime.now().date()
    offset = {0: 3, 5: 1, 6: 2}.get(today.weekday(), 1)
    return today - timedelta(days=offset)


def load_universe(conn) -> list[dict]:
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT token, symbol, trading_symbol FROM instruments WHERE is_active = TRUE")
    rows = cur.fetchall()
    cur.close()
    if not rows:
        print("instruments table is empty. Run scripts/seed_instruments.py first.")
        sys.exit(1)
    return rows


def fetch_baselines(pool: AngelSessionPool, universe: list[dict]) -> dict:
    last_day = get_last_trading_day()
    print(f"Fetching 20-day baselines as of {last_day} for {len(universe)} stocks...")
    baselines = {}
    for i, stock in enumerate(universe):
        try:
            sess = pool.get_session()
            params = {
                "exchange": "NSE",
                "symboltoken": stock["token"],
                "interval": "ONE_DAY",
                "fromdate": (last_day - timedelta(days=40)).strftime("%Y-%m-%d 09:15"),
                "todate": last_day.strftime("%Y-%m-%d 15:30"),
            }
            res = sess.getCandleData(params)
            if res.get("status") and res.get("data"):
                df = pd.DataFrame(res["data"], columns=["time", "o", "h", "l", "c", "v"])
                df["time"] = pd.to_datetime(df["time"])
                df = df[df["time"].dt.date <= last_day]
                if len(df) >= 20:
                    baselines[stock["symbol"]] = {
                        "token": stock["token"],
                        "avg_vol": df["v"].tail(20).mean(),
                        "prev_close": df.iloc[-1]["c"],
                    }
        except Exception:
            continue
        if (i + 1) % 50 == 0:
            print(f"  ...{i + 1}/{len(universe)}")
    print(f"Baselines ready for {len(baselines)} stocks.")
    return baselines


def record_signal_and_paper_buy(conn, pool, symbol, token, ltp, gap_pct, vol_mult):
    qty = int(CAPITAL_PER_TRADE / ltp)
    if qty < 1:
        return None
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO trade_signals (instrument_token, strategy_name, signal_price, technical_data)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (token, STRATEGY_NAME, ltp, psycopg2.extras.Json({"gap_pct": gap_pct, "vol_mult": vol_mult})),
    )
    signal_id = cur.fetchone()[0]
    cur.execute(
        """INSERT INTO orders (signal_id, instrument_token, is_paper, transaction_type, order_type,
                                product_type, quantity, status, status_message)
           VALUES (%s, %s, TRUE, 'BUY', 'MARKET', 'INTRADAY', %s, 'EXECUTED', 'Paper fill at LTP')
           RETURNING order_id""",
        (signal_id, token, qty),
    )
    order_id = cur.fetchone()[0]
    cur.execute(
        """INSERT INTO trades (order_id, instrument_token, execution_price, execution_time, quantity_filled)
           VALUES (%s, %s, %s, CURRENT_TIMESTAMP, %s)""",
        (order_id, token, ltp, qty),
    )
    conn.commit()
    cur.close()
    print(f"  PAPER BUY {symbol}: qty={qty} @ {ltp}  (gap={gap_pct:.2f}%, vol={vol_mult:.1f}x)")
    return {"symbol": symbol, "token": token, "qty": qty, "entry": ltp, "order_id": order_id}


def square_off_and_ledger(conn, pool, open_positions: list[dict]):
    if not open_positions:
        return
    cur = conn.cursor()
    total_gross, total_tax, wins = 0.0, 0.0, 0
    for pos in open_positions:
        try:
            sess = pool.get_session()
            q = sess.ltpData("NSE", pos["symbol"], pos["token"])
            exit_price = float(q["data"]["ltp"]) if q.get("status") else pos["entry"]
        except Exception:
            exit_price = pos["entry"]

        buy_val, sell_val = pos["qty"] * pos["entry"], pos["qty"] * exit_price
        gross = sell_val - buy_val
        taxes, breakdown = calculate_taxes(buy_val, sell_val)
        net = gross - taxes
        total_gross += gross
        total_tax += taxes
        wins += 1 if net > 0 else 0

        cur.execute(
            """UPDATE trades SET slippage = %s
               WHERE order_id = %s""",
            (exit_price - pos["entry"], pos["order_id"]),
        )
        cur.execute(
            """UPDATE trades SET brokerage=%s, stt=%s, transaction_charges=%s, sebi_fees=%s, stamp_duty=%s, gst=%s
               WHERE order_id = %s""",
            (breakdown["brokerage"], breakdown["stt"], breakdown["etc"], breakdown["sebi"],
             breakdown["stamp"], breakdown["gst"], pos["order_id"]),
        )
        print(f"  SQUARE-OFF {pos['symbol']}: exit={exit_price} net_pnl={net:.2f}")

    today = datetime.now().date()
    net_total = total_gross - total_tax
    cur.execute(
        """INSERT INTO portfolio_ledger (date, mode, opening_balance, closing_balance,
                                          total_gross_pnl, total_charges_paid, net_pnl,
                                          trades_count, win_rate)
           VALUES (%s, 'PAPER', 0, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (date, mode) DO UPDATE SET
             total_gross_pnl = portfolio_ledger.total_gross_pnl + EXCLUDED.total_gross_pnl,
             total_charges_paid = portfolio_ledger.total_charges_paid + EXCLUDED.total_charges_paid,
             net_pnl = portfolio_ledger.net_pnl + EXCLUDED.net_pnl,
             trades_count = portfolio_ledger.trades_count + EXCLUDED.trades_count""",
        (today, net_total, net_total, total_gross, total_tax, net_total,
         len(open_positions), round(100 * wins / len(open_positions), 2)),
    )
    conn.commit()
    cur.close()
    print(f"Ledger updated: {len(open_positions)} trades, net P&L {net_total:.2f}")


def main():
    creds = load_credentials_from_env()
    pool = AngelSessionPool(creds)
    pool.login_all()
    print(f"{pool.active_sessions} Angel One session(s) active.")

    conn = db_conn()
    universe = load_universe(conn)
    baselines = fetch_baselines(pool, universe)

    open_positions = []
    trades_taken = 0
    squared_off = False

    print(f"\nScanning for {MIN_GAP_PERCENT}% gap + {VOL_SHOCK_MULT}x volume shock (paper mode)...")
    try:
        while True:
            if not is_market_open():
                print("Market closed. Exiting.")
                break

            now_str = datetime.now().strftime("%H:%M")

            if now_str >= SQUARE_OFF_TIME and not squared_off:
                square_off_and_ledger(conn, pool, open_positions)
                squared_off = True
                open_positions = []

            if trades_taken < MAX_TRADES_PER_DAY and now_str < SQUARE_OFF_TIME:
                for symbol, base in baselines.items():
                    if any(p["symbol"] == symbol for p in open_positions):
                        continue
                    try:
                        sess = pool.get_session()
                        q = sess.getQuote("NSE", symbol, base["token"])
                        if not q.get("status"):
                            continue
                        data = q["data"]
                        ltp = float(data["ltp"])
                        curr_vol = float(data["volume"])
                        gap_pct = ((float(data["open"]) - base["prev_close"]) / base["prev_close"]) * 100
                        vol_mult = curr_vol / base["avg_vol"] if base["avg_vol"] else 0

                        if gap_pct > MIN_GAP_PERCENT and vol_mult > VOL_SHOCK_MULT:
                            pos = record_signal_and_paper_buy(conn, pool, symbol, base["token"], ltp, gap_pct, vol_mult)
                            if pos:
                                open_positions.append(pos)
                                trades_taken += 1
                                if trades_taken >= MAX_TRADES_PER_DAY:
                                    break
                    except Exception:
                        continue

            time.sleep(SCAN_INTERVAL_SEC)
    except KeyboardInterrupt:
        print("\nStopped by user.")
        square_off_and_ledger(conn, pool, open_positions)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
