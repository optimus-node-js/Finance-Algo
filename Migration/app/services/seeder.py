import os
import json
from sqlalchemy import text

from app.core.config import settings
from app.core.logging import log_message
from app.db.session import get_db_engines

# ==========================================
# 1. STOCKS MASTER SEEDER (Cell 8)
# ==========================================
def seed_stocks_database(create_json_files=True, input_data=None):
    """
    Seeds the REAL `instruments` / `sectors` / `index_constituents` tables
    from sql/schema.sql. Used to write to ad-hoc `stocks`/`stock_indices_map`
    tables that don't exist in the real schema -- that's what
    /market/instruments/search, the scanner, and everything else actually
    reads from, so this is the version that needs to run.
    """
    JSON_SOURCE = os.path.join(settings.DATA_DIR, 'NSE_STOCKS_SKELETON.json')
    engines = get_db_engines()

    stock_list = []
    if input_data:
        stock_list = input_data
        log_message(f"Loaded {len(stock_list)} stocks from memory.")
    elif os.path.exists(JSON_SOURCE):
        log_message(f"Loading Master JSON from {JSON_SOURCE}...")
        with open(JSON_SOURCE, 'r', encoding='utf-8') as f:
            stock_list = json.load(f)
    else:
        log_message(f"Error: No input data and {JSON_SOURCE} not found.")
        return

    for name, engine in engines.items():
        if engine is None:
            log_message(f"[{name.upper()}] Engine not available. Skipping.")
            continue

        try:
            log_message(f"[{name.upper()}] Starting upsert for {len(stock_list)} stocks...")

            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO exchanges (code, name, currency, timezone) "
                    "VALUES ('NSE', 'National Stock Exchange', 'INR', 'Asia/Kolkata') "
                    "ON CONFLICT (code) DO NOTHING"
                ))
                conn.execute(text("INSERT INTO asset_classes (name) VALUES ('EQUITY') ON CONFLICT (name) DO NOTHING"))
                exchange_id = conn.execute(text("SELECT id FROM exchanges WHERE code = 'NSE'")).scalar()
                asset_class_id = conn.execute(text("SELECT id FROM asset_classes WHERE name = 'EQUITY'")).scalar()

                sector_names = {item.get("classification", {}).get("industry")
                                 or item.get("classification", {}).get("sector")
                                 for item in stock_list}
                sector_names.discard(None)
                sector_names.discard("")
                for sector_name in sector_names:
                    conn.execute(text("INSERT INTO sectors (name) VALUES (:n) ON CONFLICT (name) DO NOTHING"),
                                 {"n": sector_name})
                sector_map = {row[1]: row[0] for row in conn.execute(text("SELECT id, name FROM sectors"))}
                index_map = {row[1].upper(): row[0] for row in conn.execute(text("SELECT id, name FROM indices"))}

                seeded = 0
                linked = 0
                for item in stock_list:
                    identifiers = item.get("identifiers", {})
                    trading = item.get("trading_info", {})
                    classif = item.get("classification", {})
                    flags = item.get("flags", {})

                    token = trading.get("token_nse")
                    angel_symbol = trading.get("angel_symbol")
                    trading_symbol = identifiers.get("symbol")
                    if not token or not angel_symbol or not trading_symbol:
                        continue  # no Angel One token match -- can't seed without a token (PK)

                    industry = classif.get("industry") or classif.get("sector")
                    sector_id = sector_map.get(industry) if industry else None
                    mkt_val = classif.get("market_cap_value")
                    if mkt_val == "":
                        mkt_val = None
                    is_active = not (flags.get("is_suspended") or flags.get("is_delisted"))

                    conn.execute(text(
                        """INSERT INTO instruments (token, symbol, trading_symbol, company_name, exchange_id,
                                                      asset_class_id, sector_id, isin, series, market_cap_category,
                                                      market_cap, lot_size, tick_size, is_cash_with_fno, is_active,
                                                      last_updated)
                           VALUES (:token, :symbol, :trading_symbol, :company_name, :exchange_id, :asset_class_id,
                                   :sector_id, :isin, :series, :market_cap_category, :market_cap, :lot_size,
                                   :tick_size, :is_cash_with_fno, :is_active, CURRENT_TIMESTAMP)
                           ON CONFLICT (token) DO UPDATE SET
                               symbol = EXCLUDED.symbol, trading_symbol = EXCLUDED.trading_symbol,
                               company_name = EXCLUDED.company_name, sector_id = EXCLUDED.sector_id,
                               isin = EXCLUDED.isin, market_cap_category = EXCLUDED.market_cap_category,
                               market_cap = EXCLUDED.market_cap, lot_size = EXCLUDED.lot_size,
                               tick_size = EXCLUDED.tick_size, is_cash_with_fno = EXCLUDED.is_cash_with_fno,
                               is_active = EXCLUDED.is_active, last_updated = CURRENT_TIMESTAMP"""
                    ), {
                        "token": token, "symbol": angel_symbol, "trading_symbol": trading_symbol,
                        "company_name": identifiers.get("company_name"), "exchange_id": exchange_id,
                        "asset_class_id": asset_class_id, "sector_id": sector_id, "isin": identifiers.get("isin"),
                        "series": (identifiers.get("active_series") or [None])[0],
                        "market_cap_category": classif.get("market_cap_category"), "market_cap": mkt_val,
                        "lot_size": trading.get("lot_size") or 1, "tick_size": trading.get("tick_size") or 0.05,
                        "is_cash_with_fno": bool(flags.get("is_fno")), "is_active": is_active,
                    })
                    seeded += 1

                    for idx_name in item.get("relations", {}).get("indices", []):
                        idx_id = index_map.get(idx_name.upper())
                        if idx_id:
                            conn.execute(text(
                                "INSERT INTO index_constituents (index_id, instrument_token) "
                                "VALUES (:idx_id, :token) ON CONFLICT DO NOTHING"
                            ), {"idx_id": idx_id, "token": token})
                            linked += 1

            log_message(f"[{name.upper()}] instruments upserted: {seeded}. index_constituents links: {linked}.")

        except Exception as e:
            log_message(f"[{name.upper()}] Seeding Failed: {e}")
