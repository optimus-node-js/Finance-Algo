"""
NSE Bhavcopy-based historical backfill for `market_data_daily`.

Why not Angel One's getCandleData for this: confirmed live in this session
that fetching history for even ~200 symbols one-by-one through Angel One's
rate-limited historical API ("Access denied because of exceeding access
rate") isn't viable for a 2000+-symbol, multi-year backfill on one working
key. NSE's own EOD bhavcopy file covers EVERY listed equity's OHLCV +
delivery data in ONE download per trading day, directly from NSE's archive
servers (archives.nseindia.com) -- unauthenticated, not rate-limited the way
Angel One's API is (unlike www.nseindia.com's dynamic API, which does block
non-browser requests -- archives.nseindia.com serves static files and does
not, verified live). ~1250 downloads for 5 years instead of ~2000 x 1250
individual rate-limited API calls.

Angel One's live API stays exactly where it already was: today's live ticks
(market_ws.py) and the on-demand cache-first candle fetch (market_data.py)
for whatever isn't covered by the backfill yet.

Two NSE archive formats, confirmed live by bisecting real URLs (not assumed
from docs -- NSE changed format and nobody publishes the exact cutover
date):
  - Modern ("sec_bhavdata_full"): plain CSV, includes delivery qty/%.
    Confirmed live: works from 2019-09-30 onward, 404s before that.
  - Legacy ("cmDDMONYYYYbhav"): CSV inside a zip, no delivery data.
    Confirmed live: works for 2016 and earlier; covers everything before
    the modern format starts.
Both get normalized to the same SYMBOL/SERIES/OPEN/HIGH/LOW/CLOSE/VOLUME/
DELIV_QTY/DELIV_PER shape so the rest of the pipeline doesn't care which
format a given day came from.
"""
from __future__ import annotations

import io
import logging
import time
import zipfile
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
import psycopg2.extras
import requests

from app.db.session import get_raw_connection

logger = logging.getLogger("bhavcopy")

MODERN_FORMAT_START = date(2019, 9, 30)
MODERN_URL = "https://archives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"
LEGACY_URL = "https://nsearchives.nseindia.com/content/historical/EQUITIES/{yyyy}/{mon}/cm{ddmonyyyy}bhav.csv.zip"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _fetch_modern(trade_date: date) -> Optional[pd.DataFrame]:
    url = MODERN_URL.format(ddmmyyyy=trade_date.strftime("%d%m%Y"))
    resp = requests.get(url, headers=HEADERS, timeout=20)
    if resp.status_code != 200:
        return None
    df = pd.read_csv(io.StringIO(resp.content.decode("utf-8")))
    df.columns = [c.strip() for c in df.columns]
    df = df[df["SERIES"].str.strip() == "EQ"]
    if df.empty:
        return None
    return pd.DataFrame({
        "SYMBOL": df["SYMBOL"].str.strip(), "SERIES": df["SERIES"].str.strip(),
        "OPEN": df["OPEN_PRICE"], "HIGH": df["HIGH_PRICE"], "LOW": df["LOW_PRICE"], "CLOSE": df["CLOSE_PRICE"],
        "VOLUME": df["TTL_TRD_QNTY"], "DELIV_QTY": df["DELIV_QTY"], "DELIV_PER": df["DELIV_PER"],
    })


def _fetch_legacy(trade_date: date) -> Optional[pd.DataFrame]:
    ddmonyyyy = trade_date.strftime("%d%b%Y").upper()
    url = LEGACY_URL.format(yyyy=trade_date.year, mon=trade_date.strftime("%b").upper(), ddmonyyyy=ddmonyyyy)
    resp = requests.get(url, headers=HEADERS, timeout=20)
    if resp.status_code != 200:
        return None
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        with z.open(z.namelist()[0]) as f:
            df = pd.read_csv(f)
    df.columns = [c.strip() for c in df.columns]
    df = df[df["SERIES"].str.strip() == "EQ"]
    if df.empty:
        return None
    return pd.DataFrame({
        "SYMBOL": df["SYMBOL"].str.strip(), "SERIES": df["SERIES"].str.strip(),
        "OPEN": df["OPEN"], "HIGH": df["HIGH"], "LOW": df["LOW"], "CLOSE": df["CLOSE"],
        "VOLUME": df["TOTTRDQTY"], "DELIV_QTY": None, "DELIV_PER": None,
    })


def fetch_bhavcopy(trade_date: date, _retried: bool = False) -> Optional[pd.DataFrame]:
    """One day's full-market EOD file, normalized regardless of which NSE
    archive format applies. Returns None for weekends/holidays (NSE simply
    404s -- not an error, just no trading that day). Retries once on a
    transient network failure (connection reset, timeout) -- distinct from a
    404, which means "no data for this day" and shouldn't be retried."""
    try:
        if trade_date >= MODERN_FORMAT_START:
            return _fetch_modern(trade_date)
        return _fetch_legacy(trade_date)
    except Exception:
        if not _retried:
            time.sleep(2)
            return fetch_bhavcopy(trade_date, _retried=True)
        logger.exception("bhavcopy fetch failed for %s (after retry)", trade_date)
        return None


def _upsert_day(conn, trade_date: date, df: pd.DataFrame) -> int:
    cur = conn.cursor()
    cur.execute("SELECT symbol, token FROM instruments")
    token_by_symbol = {row[0].split("-")[0]: row[1] for row in cur.fetchall()}

    rows = []
    for _, r in df.iterrows():
        token = token_by_symbol.get(r["SYMBOL"])
        if not token:
            continue
        deliv_qty = r.get("DELIV_QTY")
        deliv_per = r.get("DELIV_PER")
        rows.append((
            token, trade_date,
            float(r["OPEN"]), float(r["HIGH"]), float(r["LOW"]), float(r["CLOSE"]),
            int(r["VOLUME"]),
            int(deliv_qty) if pd.notna(deliv_qty) and str(deliv_qty).strip() != "-" else None,
            float(deliv_per) if pd.notna(deliv_per) and str(deliv_per).strip() != "-" else None,
        ))
    if not rows:
        return 0

    psycopg2.extras.execute_values(
        cur,
        """INSERT INTO market_data_daily
               (instrument_token, date, open, high, low, close, volume, delivery_qty, delivery_percentage)
           VALUES %s
           ON CONFLICT (instrument_token, date) DO UPDATE SET
               open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
               volume = EXCLUDED.volume, delivery_qty = EXCLUDED.delivery_qty,
               delivery_percentage = EXCLUDED.delivery_percentage""",
        rows,
    )
    conn.commit()
    cur.close()
    return len(rows)


def _upsert_job_status(**fields):
    conn = get_raw_connection("local")
    cur = conn.cursor()
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(f"%({k})s" for k in fields)
    updates = ", ".join(f"{k} = EXCLUDED.{k}" for k in fields if k != "job_name")
    cur.execute(
        f"INSERT INTO sync_jobs ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT (job_name) DO UPDATE SET {updates}",
        fields,
    )
    conn.commit()
    cur.close()
    conn.close()


def run_backfill_tracked(job_name: str, start: date, end: date) -> dict:
    """Same as backfill_range, but keeps sync_jobs updated as it goes --
    lets a multi-hour run be observed via GET /seed/sync-status instead of
    tailing logs."""
    _upsert_job_status(job_name=job_name, status="running", last_run_at=datetime.now(),
                        days_total=(end - start).days + 1, days_done=0, rows_processed=0, last_error=None)

    def progress_cb(trade_date, days_done, days_total, rows_total):
        if days_done % 20 == 0 or days_done == days_total:
            _upsert_job_status(job_name=job_name, days_done=days_done, days_total=days_total,
                                rows_processed=rows_total, last_synced_date=trade_date)

    try:
        result = backfill_range(start, end, progress_cb=progress_cb)
        _upsert_job_status(job_name=job_name, status="success", last_synced_date=end,
                            rows_processed=result["rows_total"], days_done=result["days_written"] + result["days_skipped"])
        return result
    except Exception as e:
        logger.exception("run_backfill_tracked failed for %s", job_name)
        _upsert_job_status(job_name=job_name, status="failed", last_error=str(e))
        raise


def sync_incremental(job_name: str = "bhavcopy_daily_sync") -> dict:
    """Catches up from wherever this job last left off (any gap size --
    ran yesterday, ran two weeks ago, doesn't matter) up through today.
    First run ever (no last_synced_date) defaults to a 30-day lookback."""
    conn = get_raw_connection("local")
    cur = conn.cursor()
    cur.execute("SELECT last_synced_date FROM sync_jobs WHERE job_name = %s", (job_name,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    start = (row[0] + timedelta(days=1)) if row and row[0] else (date.today() - timedelta(days=30))
    end = date.today()
    if start > end:
        return {"status": "already_up_to_date", "last_synced_date": str(row[0]) if row else None}
    return run_backfill_tracked(job_name, start, end)


def backfill_range(start: date, end: date, progress_cb=None) -> dict:
    """Iterates trading days start..end (inclusive), skipping weekends and
    tolerating holiday 404s, upserting each day's full market into
    market_data_daily. progress_cb(trade_date, days_done, days_total,
    rows_total), if given, is called after every day (written or skipped) --
    used by the /seed/backfill-history job-tracking wrapper."""
    conn = get_raw_connection("local")
    days_written = days_skipped = rows_total = 0
    total_days = (end - start).days + 1
    d = start
    day_idx = 0
    while d <= end:
        day_idx += 1
        if d.weekday() >= 5:
            d += timedelta(days=1)
            continue
        df = fetch_bhavcopy(d)
        if df is None:
            days_skipped += 1
        else:
            n = _upsert_day(conn, d, df)
            rows_total += n
            days_written += 1
            logger.info("bhavcopy %s: %d rows upserted", d, n)
        if progress_cb:
            progress_cb(d, day_idx, total_days, rows_total)
        d += timedelta(days=1)
    conn.close()
    return {"days_written": days_written, "days_skipped": days_skipped, "rows_total": rows_total,
            "range": [str(start), str(end)]}
