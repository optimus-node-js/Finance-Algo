"""
Seed the `instruments` table from the already-downloaded Nifty 500 token map
(Trading/Stocks/data/nifty500.json). This is the "Migration" step Tadka
described: give the database full context of every ticker/token so later
strategy + Angel One order calls can look up the exact instrument by symbol
instead of re-scraping NSE/Angel One every time.

Runs entirely offline against local static JSON - no live network calls.
Safe to re-run (idempotent via ON CONFLICT).

Usage:
    DB_HOST=localhost DB_USER=postgres DB_PASS=postgres DB_NAME=algotrading \
    python3 seed_instruments.py /path/to/nifty500.json
"""
import json
import os
import sys

import psycopg2

DB_HOST = os.environ.get("LOCAL_DB_HOST", os.environ.get("DB_HOST", "localhost"))
DB_PORT = os.environ.get("LOCAL_DB_PORT", "5432")
DB_USER = os.environ.get("LOCAL_DB_USER", "postgres")
DB_PASS = os.environ.get("LOCAL_DB_PASS", "postgres")
DB_NAME = os.environ.get("LOCAL_DB_NAME", "algotrading")


def main(json_path: str) -> None:
    with open(json_path) as f:
        stocks = json.load(f)

    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS, dbname=DB_NAME
    )
    conn.autocommit = False
    cur = conn.cursor()

    # 1. Reference rows (idempotent)
    cur.execute(
        """
        INSERT INTO exchanges (code, name, currency, timezone)
        VALUES ('NSE', 'National Stock Exchange', 'INR', 'Asia/Kolkata')
        ON CONFLICT (code) DO NOTHING
        """
    )
    cur.execute(
        "INSERT INTO asset_classes (name) VALUES ('EQUITY') ON CONFLICT (name) DO NOTHING"
    )
    cur.execute("SELECT id FROM exchanges WHERE code = 'NSE'")
    exchange_id = cur.fetchone()[0]
    cur.execute("SELECT id FROM asset_classes WHERE name = 'EQUITY'")
    asset_class_id = cur.fetchone()[0]

    # 2. Upsert every instrument
    inserted = 0
    for s in stocks:
        cur.execute(
            """
            INSERT INTO instruments (token, symbol, trading_symbol, company_name,
                                      exchange_id, asset_class_id, is_active, last_updated)
            VALUES (%s, %s, %s, %s, %s, %s, TRUE, CURRENT_TIMESTAMP)
            ON CONFLICT (token) DO UPDATE SET
                symbol = EXCLUDED.symbol,
                trading_symbol = EXCLUDED.trading_symbol,
                company_name = EXCLUDED.company_name,
                is_active = TRUE,
                last_updated = CURRENT_TIMESTAMP
            """,
            (
                s["token"],
                s["symbol"],
                s.get("clean_symbol"),
                s.get("company_name"),
                exchange_id,
                asset_class_id,
            ),
        )
        inserted += 1

    conn.commit()
    cur.execute("SELECT COUNT(*) FROM instruments")
    total = cur.fetchone()[0]
    print(f"Upserted {inserted} instruments from {json_path}. Table now has {total} rows.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "data/nifty500.json"
    main(path)
