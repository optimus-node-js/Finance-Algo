from fastapi import APIRouter

from app.db.session import get_raw_connection

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])


@router.get("/ledger")
def ledger(mode: str = "PAPER", days: int = 30):
    conn = get_raw_connection("local")
    cur = conn.cursor()
    cur.execute(
        """SELECT date, mode, closing_balance, total_gross_pnl, total_charges_paid,
                  net_pnl, trades_count, win_rate
           FROM portfolio_ledger WHERE mode = %s ORDER BY date DESC LIMIT %s""",
        (mode, days),
    )
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


@router.get("/summary")
def summary(mode: str = "PAPER"):
    conn = get_raw_connection("local")
    cur = conn.cursor()
    cur.execute(
        """SELECT COUNT(*) AS days_traded, COALESCE(SUM(net_pnl), 0) AS total_net_pnl,
                  COALESCE(SUM(trades_count), 0) AS total_trades,
                  COALESCE(AVG(win_rate), 0) AS avg_win_rate
           FROM portfolio_ledger WHERE mode = %s""",
        (mode,),
    )
    cols = [d[0] for d in cur.description]
    row = dict(zip(cols, cur.fetchone()))
    cur.close()
    conn.close()
    return row
