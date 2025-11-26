-- =============================================
-- 1. CORE REFERENCE TABLES (Static Data)
-- =============================================

-- Exchanges: NSE, BSE, MCX, etc.
CREATE TABLE exchanges (
    id SERIAL PRIMARY KEY,
    code VARCHAR(10) NOT NULL UNIQUE, -- 'NSE', 'BSE'
    name VARCHAR(100),
    currency VARCHAR(10) DEFAULT 'INR',
    timezone VARCHAR(50) DEFAULT 'Asia/Kolkata',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Asset Classes: Equity, Future, Option, Index, etc.
CREATE TABLE asset_classes (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE, -- 'EQUITY', 'FUTURE', 'OPTION', 'INDEX'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Sectors: Banking, IT, Pharma, etc. (from 5paisa)
CREATE TABLE sectors (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE, -- 'Nifty Bank', 'IT'
    parent_sector_id INT REFERENCES sectors(id), -- Hierarchy (e.g., PSU Bank -> Bank)
    source_url VARCHAR(255), -- Link to 5paisa sector page
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================
-- 2. THE GOLDEN STOCK TABLE (Stocks)
-- =============================================

-- This replaces your JSON file. Stores EVERYTHING about a stock.
CREATE TABLE stocks (
    token VARCHAR(50) PRIMARY KEY, -- Angel One Token ID (e.g., '3045')
    symbol VARCHAR(50) NOT NULL, -- 'RELIANCE-EQ'
    trading_symbol VARCHAR(50), -- 'RELIANCE' (Clean symbol)
    company_name VARCHAR(255),
    exchange_id INT REFERENCES exchanges(id),
    asset_class_id INT REFERENCES asset_classes(id),
    sector_id INT REFERENCES sectors(id),
    
    -- Detailed Info from Angel One API / 5Paisa
    isin VARCHAR(20), -- Critical for mapping
    scrip_type VARCHAR(50), -- 'nseCash', 'bseCash'
    series VARCHAR(10), -- 'EQ', 'BE', 'BL'
    market_cap_category VARCHAR(50), -- 'Large Cap', 'Mid Cap', 'Small Cap'
    market_cap NUMERIC(20, 2), -- Actual Value
    lot_size INT DEFAULT 1,
    tick_size NUMERIC(10, 4) DEFAULT 0.05,
    is_cash_with_fno BOOLEAN DEFAULT FALSE, -- Available in F&O?
    
    -- Status
    is_active BOOLEAN DEFAULT TRUE,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for fast searching
CREATE INDEX idx_instruments_symbol ON instruments(symbol);
CREATE INDEX idx_instruments_sector ON instruments(sector_id);
CREATE INDEX idx_instruments_isin ON instruments(isin);

-- =============================================
-- 3. INDICES & BENCHMARKS
-- =============================================

-- Indices: Nifty 50, Sensex, etc.
CREATE TABLE indices (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE, -- 'Nifty 50'
    token VARCHAR(50), -- Angel One Token for Index
    exchange_id INT REFERENCES exchanges(id),
    category VARCHAR(50), -- 'Indian', 'Global', 'Sectoral'
    source_url VARCHAR(255), -- 5paisa link
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Mapping: Which stocks are in which Index?
CREATE TABLE index_constituents (
    index_id INT REFERENCES indices(id),
    instrument_token VARCHAR(50) REFERENCES instruments(token),
    weightage NUMERIC(5, 2), -- Percentage weight in index
    added_on DATE DEFAULT CURRENT_DATE,
    PRIMARY KEY (index_id, instrument_token)
);

-- =============================================
-- 4. MARKET DATA TABLES (Time Series)
-- =============================================

-- Daily EOD Data (Historical Candles)
CREATE TABLE market_data_daily (
    id BIGSERIAL PRIMARY KEY,
    instrument_token VARCHAR(50) REFERENCES instruments(token),
    date DATE NOT NULL,
    open NUMERIC(10, 2),
    high NUMERIC(10, 2),
    low NUMERIC(10, 2),
    close NUMERIC(10, 2),
    volume BIGINT,
    delivery_qty BIGINT, -- For Swing Trading analysis
    delivery_percentage NUMERIC(5, 2),
    UNIQUE(instrument_token, date) -- Prevent duplicates
);

-- Intraday Data (1-Min / 5-Min Candles) - High Volume Table
CREATE TABLE market_data_intraday (
    id BIGSERIAL PRIMARY KEY,
    instrument_token VARCHAR(50) REFERENCES instruments(token),
    timestamp TIMESTAMP NOT NULL,
    timeframe VARCHAR(10), -- '1m', '5m', '15m'
    open NUMERIC(10, 2),
    high NUMERIC(10, 2),
    low NUMERIC(10, 2),
    close NUMERIC(10, 2),
    volume BIGINT
);
CREATE INDEX idx_intraday_token_time ON market_data_intraday(instrument_token, timestamp);

-- Live Snapshots (The "Fresh" Data for Dashboard)
CREATE TABLE market_snapshots_live (
    instrument_token VARCHAR(50) PRIMARY KEY REFERENCES instruments(token),
    ltp NUMERIC(10, 2),
    change_percent NUMERIC(5, 2),
    total_buy_qty BIGINT,
    total_sell_qty BIGINT,
    atp NUMERIC(10, 2), -- Avg Traded Price
    open_interest BIGINT, -- For F&O
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================
-- 5. 5PAISA INTELLIGENCE (Scraped Lists)
-- =============================================

-- Metadata for the 5paisa lists (URLs & Frequency)
CREATE TABLE market_lists_meta (
    id SERIAL PRIMARY KEY,
    list_name VARCHAR(100) NOT NULL UNIQUE, -- '52 Week High', 'Volume Shockers'
    source_url VARCHAR(500) NOT NULL,
    update_frequency VARCHAR(50), -- 'Daily', 'Live', 'Weekly'
    last_scraped_at TIMESTAMP
);

-- The actual stocks inside those lists (History Tracking)
CREATE TABLE market_list_constituents (
    id BIGSERIAL PRIMARY KEY,
    list_id INT REFERENCES market_lists_meta(id),
    instrument_token VARCHAR(50) REFERENCES instruments(token),
    date_added DATE DEFAULT CURRENT_DATE,
    is_current BOOLEAN DEFAULT TRUE, -- Is it in the list TODAY?
    removed_on DATE
);
-- This lets you query: "Show me stocks that were in 'Volume Shockers' yesterday"

-- =============================================
-- 6. F&O / DERIVATIVES SPECIALS
-- =============================================

CREATE TABLE option_chain (
    id BIGSERIAL PRIMARY KEY,
    underlying_token VARCHAR(50) REFERENCES instruments(token), -- Nifty 50 or Reliance
    derivative_token VARCHAR(50) REFERENCES instruments(token), -- The Option Contract
    expiry_date DATE,
    strike_price NUMERIC(10, 2),
    option_type VARCHAR(2) CHECK (option_type IN ('CE', 'PE')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =============================================
-- 7. TRADING JOURNAL & PERFORMANCE TRACKING
-- =============================================

-- Signals generated by your Algorithm
CREATE TABLE trade_signals (
    id BIGSERIAL PRIMARY KEY,
    instrument_token VARCHAR(50) REFERENCES instruments(token),
    strategy_name VARCHAR(100), -- 'GapVolShock_v1'
    signal_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    signal_price NUMERIC(10, 2),
    
    -- Detailed Technicals at Signal Time (JSON for flexibility)
    technical_data JSONB, 
    -- Example: {"gap_pct": 1.5, "vol_mult": 3.2, "vwap": 1200, "rsi": 65}
    
    is_processed BOOLEAN DEFAULT FALSE -- Has the bot placed an order for this?
);

-- Orders Placed (Real or Paper)
CREATE TABLE orders (
    order_id BIGSERIAL PRIMARY KEY,
    signal_id BIGINT REFERENCES trade_signals(id),
    broker_order_id VARCHAR(100), -- Angel One Order ID
    instrument_token VARCHAR(50) REFERENCES instruments(token),
    
    transaction_type VARCHAR(10), -- 'BUY', 'SELL'
    order_type VARCHAR(20), -- 'MARKET', 'LIMIT', 'SL'
    product_type VARCHAR(20), -- 'INTRADAY', 'DELIVERY'
    
    quantity INT NOT NULL,
    price_requested NUMERIC(10, 2), -- Limit Price (if any)
    trigger_price NUMERIC(10, 2), -- SL Trigger (if any)
    
    status VARCHAR(20), -- 'PENDING', 'EXECUTED', 'REJECTED', 'CANCELLED'
    status_message TEXT, -- Error message from Broker
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Trades Executed (The Fill)
CREATE TABLE trades (
    trade_id BIGSERIAL PRIMARY KEY,
    order_id BIGINT REFERENCES orders(order_id),
    instrument_token VARCHAR(50) REFERENCES instruments(token),
    
    execution_price NUMERIC(10, 2),
    execution_time TIMESTAMP,
    quantity_filled INT,
    
    -- Metrics
    slippage NUMERIC(10, 2), -- (Execution Price - Signal Price)
    
    -- Cost Breakdown
    brokerage NUMERIC(10, 2) DEFAULT 0,
    stt NUMERIC(10, 2) DEFAULT 0,
    transaction_charges NUMERIC(10, 2) DEFAULT 0,
    sebi_fees NUMERIC(10, 2) DEFAULT 0,
    stamp_duty NUMERIC(10, 2) DEFAULT 0,
    gst NUMERIC(10, 2) DEFAULT 0,
    total_charges NUMERIC(10, 2) GENERATED ALWAYS AS (brokerage + stt + transaction_charges + sebi_fees + stamp_duty + gst) STORED
);

-- Portfolio Ledger (Daily P&L Tracking)
CREATE TABLE portfolio_ledger (
    id SERIAL PRIMARY KEY,
    date DATE UNIQUE NOT NULL,
    opening_balance NUMERIC(15, 2),
    closing_balance NUMERIC(15, 2),
    
    total_gross_pnl NUMERIC(15, 2),
    total_charges_paid NUMERIC(15, 2),
    net_pnl NUMERIC(15, 2),
    roi_percentage NUMERIC(5, 2),
    
    trades_count INT,
    win_rate NUMERIC(5, 2)
);

-- =============================================
-- 8. FINANCIAL LINKS (Resources)
-- =============================================

CREATE TABLE financial_resources (
    id SERIAL PRIMARY KEY,
    category VARCHAR(100), -- 'Sector', 'Research', 'News'
    title VARCHAR(255),
    url VARCHAR(500) NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);