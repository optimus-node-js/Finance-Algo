import os
import json
import datetime
from sqlalchemy import MetaData, Table, Column, Integer, String, Float, Boolean, JSON, ForeignKey, UniqueConstraint, inspect, BigInteger, DateTime, Text, text
from sqlalchemy.dialects.postgresql import insert

from app.core.config import settings
from app.core.logging import log_message
from app.db.session import get_db_engines

# ==========================================
# 1. STOCKS MASTER SEEDER (Cell 8)
# ==========================================
def seed_stocks_database(create_json_files=True, input_data=None):
    """
    Seeds the 'stocks' and 'stock_indices_map' tables in Postgres.
    """
    JSON_SOURCE = os.path.join(settings.DATA_DIR, 'NSE_STOCKS_SKELETON.json')
    engines = get_db_engines()

    def init_schema(engine):
        metadata = MetaData()
        
        inspector = inspect(engine)
        if not inspector.has_table("indices"):
             log_message(f"❌ CRITICAL ERROR: Table 'indices' not found. Run Cell 4 first!")
             raise Exception("Table 'indices' not found")
        
        # Reflect existing 'indices' table
        indices_table = Table('indices', metadata, autoload_with=engine)
        log_message("✅ Detected existing 'indices' table.")

        stocks_table = Table(
            'stocks', metadata,
            Column('id', Integer, primary_key=True, autoincrement=True),
            Column('master_key', String),
            Column('symbol', String, unique=True, nullable=False),
            Column('isin', String),
            Column('slb_isin', String),
            Column('company_name', String),
            Column('is_eq', Boolean, default=False),
            Column('is_be', Boolean, default=False),
            Column('is_bl', Boolean, default=False),
            Column('is_bz', Boolean, default=False),
            Column('token_nse', String),
            Column('token_bse', String),
            Column('tick_size', Float),
            Column('lot_size', Integer),
            Column('listing_date', String),
            Column('currency', String),
            Column('segment', String),
            Column('industry', String),
            Column('sector', String),
            Column('market_cap_category', String),
            Column('market_cap_value', Float),
            Column('is_fno', Boolean),
            Column('is_etf', Boolean),
            Column('is_slb_eligible', Boolean),
            Column('is_mtf_eligible', Boolean),
            Column('is_suspended', Boolean),
            Column('is_delisted', Boolean),
            Column('is_debt', Boolean),
            Column('is_municipal_bond', Boolean),
            Column('meta', JSON) 
        )
        
        stock_indices_map = Table(
            'stock_indices_map', metadata,
            Column('id', Integer, primary_key=True, autoincrement=True),
            Column('stock_id', Integer, ForeignKey('stocks.id', ondelete="CASCADE")),
            Column('index_id', Integer, ForeignKey('indices.id', ondelete="CASCADE")),
            UniqueConstraint('stock_id', 'index_id', name='uq_stock_index_pair')
        )

        inspector = inspect(engine)
        if inspector.has_table("stock_indices_map"):
            columns = [c['name'] for c in inspector.get_columns("stock_indices_map")]
            if "stock_id" not in columns:
                log_message("⚠️ Detected incompatible schema for 'stock_indices_map'. Dropping table to recreate...")
                with engine.begin() as conn:
                    conn.execute(text("DROP TABLE stock_indices_map CASCADE"))
        
        metadata.create_all(engine)
        log_message("✅ Database Schema (stocks, stock_indices_map) Verified.")
        return stocks_table, stock_indices_map

    stock_list = []
    if input_data:
        stock_list = input_data
        log_message(f"📖 Loaded {len(stock_list)} stocks from memory.")
    elif os.path.exists(JSON_SOURCE):
        log_message(f"📖 Loading Master JSON from {JSON_SOURCE}...")
        with open(JSON_SOURCE, 'r', encoding='utf-8') as f:
            stock_list = json.load(f)
    else:
        log_message(f"❌ Error: No input data and {JSON_SOURCE} not found.")
        return

    for name, engine in engines.items():
        if engine is None:
            log_message(f"⚠️ [{name.upper()}] Engine not available. Skipping.")
            continue

        try:
            log_message(f"🚀 [{name.upper()}] Starting Bulk Upsert for {len(stock_list)} stocks...")
            
            stocks_table, relations_table = init_schema(engine)
            
            index_map = {}
            try:
                with engine.connect() as conn:
                    if not engine.dialect.has_table(conn, "indices"):
                         log_message(f"⚠️ [{name.upper()}] 'indices' table missing. Skipping relations.")
                    else:
                        result = conn.execute(text("SELECT name, id FROM indices"))
                        for row in result:
                            index_map[row[0].upper()] = row[1]
                        log_message(f"🔗 [{name.upper()}] Found {len(index_map)} indices available for linking.")
            except Exception as e:
                 log_message(f"⚠️ [{name.upper()}] Failed to fetch indices map: {e}")

            stock_records_batch = []
            relation_records_batch = []

            for item in stock_list:
                identifiers = item.get("identifiers", {})
                trading = item.get("trading_info", {})
                classif = item.get("classification", {})
                flags = item.get("flags", {})
                
                symbol = identifiers.get("symbol")
                if not symbol: continue
                
                mkt_val = classif.get("market_cap_value")
                if mkt_val == "": mkt_val = None
                
                is_eq = "EQ" in identifiers.get("active_series", [])
                is_be = "BE" in identifiers.get("active_series", [])
                is_bl = "BL" in identifiers.get("active_series", [])
                is_bz = "BZ" in identifiers.get("active_series", [])

                record = {
                    "master_key": item.get("master_key"),
                    "symbol": symbol,
                    "isin": identifiers.get("isin"),
                    "slb_isin": identifiers.get("slb_isin"),
                    "company_name": identifiers.get("company_name"),
                    "is_eq": is_eq, "is_be": is_be, "is_bl": is_bl, "is_bz": is_bz,
                    "token_nse": trading.get("token_nse"),
                    "token_bse": trading.get("token_bse"),
                    "tick_size": trading.get("tick_size"),
                    "lot_size": trading.get("lot_size"),
                    "listing_date": trading.get("listing_date"),
                    "currency": trading.get("currency"),
                    "segment": trading.get("segment"),
                    "industry": classif.get("industry"),
                    "sector": classif.get("sector"),
                    "market_cap_category": classif.get("market_cap_category"),
                    "market_cap_value": mkt_val,
                    "is_fno": flags.get("is_fno"),
                    "is_etf": flags.get("is_etf"),
                    "is_slb_eligible": flags.get("is_slb_eligible"),
                    "is_mtf_eligible": flags.get("is_mtf_eligible"),
                    "is_suspended": flags.get("is_suspended"),
                    "is_delisted": flags.get("is_delisted"),
                    "is_debt": flags.get("is_debt"),
                    "is_municipal_bond": flags.get("is_municipal_bond"),
                    "meta": json.dumps(item.get("__RAW_DATA_SOURCES__", {}))
                }
                stock_records_batch.append(record)

            with engine.begin() as conn:
                if stock_records_batch:
                    stmt = insert(stocks_table).values(stock_records_batch)
                    update_stmt = stmt.on_conflict_do_update(
                        index_elements=['symbol'],
                        set_={
                            "token_nse": stmt.excluded.token_nse,
                            "token_bse": stmt.excluded.token_bse,
                            "market_cap_value": stmt.excluded.market_cap_value,
                            "is_fno": stmt.excluded.is_fno,
                            "meta": stmt.excluded.meta
                        }
                    )
                    conn.execute(update_stmt)
            
            log_message(f"✅ [{name.upper()}] Stocks Upserted. Processing Relations...")

            # --- RELATIONS MAPPING ---
            if index_map:
                with engine.connect() as conn:
                    stock_id_map = {}
                    result = conn.execute(text("SELECT symbol, id FROM stocks"))
                    for row in result:
                        stock_id_map[row[0]] = row[1]
                
                for item in stock_list:
                    symbol = item.get("identifiers", {}).get("symbol")
                    stock_id = stock_id_map.get(symbol)
                    if not stock_id: continue
                    
                    indices = item.get("relations", {}).get("indices", [])
                    for idx_name in indices:
                        idx_id = index_map.get(idx_name.upper())
                        if idx_id:
                            relation_records_batch.append({"stock_id": stock_id, "index_id": idx_id})

                with engine.begin() as conn:
                    # Clear old relations? Maybe not for now, just upsert/ignore
                    # For now, let's just insert and ignore duplicates
                    if relation_records_batch:
                        stmt = insert(relations_table).values(relation_records_batch)
                        do_nothing_stmt = stmt.on_conflict_do_nothing(
                            constraint='uq_stock_index_pair'
                        )
                        conn.execute(do_nothing_stmt)
                
                log_message(f"🔗 [{name.upper()}] Relations Updated: {len(relation_records_batch)} links.")

        except Exception as e:
            log_message(f"❌ [{name.upper()}] Seeding Failed: {e}")
