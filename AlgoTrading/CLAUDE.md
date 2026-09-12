# AlgoTrading — Project Context for Claude Code

Read this file first. It's the single source of truth for what this project is,
what's built, what's verified, and what's left. Written 2026-07-23 by a Cowork
session that built the backend but could not run it live (see "Network" below) —
that's now your job, since you're running with real internet on Tadka's machine.

## Goal

An automated algo-trading platform for Indian equities (NSE) on Angel One
SmartAPI. Paper trading first — auto-executes on signal, no per-trade
confirmation needed, everything logged to Postgres. Tadka watches/reviews via
chat now, an Expo mobile app later. Live trading is a future phase, gated on a
proven paper track record. First priority (current phase): get this backend
100% working end-to-end locally. Do NOT start the Expo app before that's true.

## Folder layout

```
Finance Server/
├── AlgoTrading/
│   ├── CLAUDE.md              <- you are here
│   └── AlgoBackend/           <- THE backend. Run everything from inside here.
│       ├── app/
│       │   ├── main.py                 FastAPI entrypoint - includes all routers
│       │   ├── core/
│       │   │   ├── config.py           reads .env into a Settings object
│       │   │   └── logging.py          (pre-existing) DB-backed logging helpers
│       │   ├── db/
│       │   │   └── session.py          SQLAlchemy engines (local + Neon) +
│       │   │                           get_raw_connection() for psycopg2 cursors
│       │   ├── routers/
│       │   │   ├── seed.py, clean.py, logs.py     (pre-existing, NSE data seeding)
│       │   │   ├── market.py           GET /market/instruments/count, /market/momentum
│       │   │   ├── trading.py          POST/GET /trading/scanner/{start,stop,status}, /trading/positions
│       │   │   └── portfolio.py        GET /portfolio/ledger, /portfolio/summary
│       │   └── services/
│       │       ├── nse_fetcher.py, enrichment.py, seeder.py   (pre-existing seeding pipeline)
│       │       ├── angel_auth.py       TOTP multi-key (x5) SmartConnect session pool
│       │       ├── market_data.py      bulk quotes (getMarketData) + historical candles
│       │       ├── strategy_engine.py  pluggable Strategy ABC; GapVolumeShockStrategy + rank_momentum()
│       │       ├── paper_trading.py    fixed-size paper order engine + tax calc + ledger writes
│       │       └── scanner_scheduler.py  APScheduler job wiring market_data + strategy_engine + paper_trading together
│       ├── sql/schema.sql      Postgres schema — instruments, market data, signals, orders, trades, portfolio_ledger
│       ├── scripts/seed_instruments.py   offline seeding from a nifty500.json token map
│       ├── requirements.txt
│       ├── .env                REAL credentials (Angel One x5 keys, local + Neon Postgres) — already here, already read
│       └── .env.example
├── Trading/
│   ├── Stocks/main.ipynb       original research notebook: Gap+VolShock strategy, backtest, live engine draft
│   ├── Stocks/data/            nifty500.json (token map), backtest_results.csv, real_trade_ledger.csv, etc.
│   └── Options/                separate options-trading research (Greeks, risk manager) — not wired into AlgoBackend yet
└── Migration/                  SUPERSEDED — this is the old location before the AlgoTrading/AlgoBackend
                                 restructure. Same files exist there too (not deleted, Claude can't delete files
                                 on this machine). Ignore it; work only in AlgoBackend. Tadka can delete
                                 Migration/ manually once he's confirmed AlgoBackend works.
```

## How to run it

```bash
cd "Finance Server/AlgoTrading/AlgoBackend"
pip install -r requirements.txt

# one-time: create the DB and load the schema
createdb algotrading      # or use LOCAL_DB_NAME from .env if different
psql -U postgres -d algotrading -f sql/schema.sql

# one-time: seed the instrument universe (offline, no network needed - uses the
# token map already in Trading/Stocks/data/)
python scripts/seed_instruments.py "../../Trading/Stocks/data/nifty500.json"

# start the backend
uvicorn app.main:app --reload --port 8000
```

Then, during NSE market hours (9:15–15:30 IST):
```
POST http://localhost:8000/trading/scanner/start
GET  http://localhost:8000/trading/scanner/status
GET  http://localhost:8000/trading/positions
GET  http://localhost:8000/portfolio/summary
POST http://localhost:8000/trading/scanner/stop
```

`.env` already has real Angel One credentials (5-key rotation pool, client
code ***REDACTED***) and DB config (`LOCAL_DB_*`, `NEON_DB_URL`). Nothing else to
fill in to do a first run.

## What's DONE and verified

- Postgres schema fixed and applied cleanly (17 tables). Original bug: table was
  named `stocks` but every FK/index referenced `instruments` — renamed, would not
  have run otherwise.
- Added `orders.is_paper` (default TRUE) and `portfolio_ledger.mode` (PAPER/LIVE,
  unique per date+mode) so paper and real money can never share a balance.
- `instruments` table seedable offline from `nifty500.json` — verified, 481 rows,
  symbol→token lookups correct (e.g. RELIANCE-EQ → token 2885).
- `angel_auth.py` — loads all 5 real credential sets from `.env`, generates valid
  TOTP codes. The actual login call itself is UNTESTED (see Network below).
- `market_data.py` — **found and fixed a real bug**: the original notebook's live
  engine called `session.getQuote(...)`, which does not exist on the installed
  smartapi-python SDK (confirmed via `dir(SmartConnect)` — would have crashed the
  first time it ran live). Replaced with the correct bulk endpoint,
  `getMarketData(mode="FULL", exchangeTokens=...)` — up to 50 symbols/request,
  1 req/sec (per Angel One's SmartAPI forum). `getCandleData` for historical
  baselines respects the documented 3 req/sec limit.
- `paper_trading.py` — full signal→order→trade→ledger cycle tested end-to-end
  against real Postgres with synthetic quote data (not live Angel One data).
  **Found and fixed a real bug** during this test: the portfolio_ledger INSERT
  had a duplicated parameter causing a placeholder/argument-count mismatch
  (`TypeError: not all arguments converted during string formatting`).
- All 3 new routers (market/trading/portfolio) booted via FastAPI TestClient and
  responded correctly against real Postgres.
- Fixed capital sizing only (`CAPITAL_PER_TRADE`, default ₹20,000) — deliberately
  NOT compounding, NOT leveraged. See "Backtest validation" below for why.

## What's NOT verified (do this first)

Nothing that requires live internet has been tested — the Cowork cloud sandbox
that built this could only reach pypi.org/github.com (verified against 8+
domains including Angel One, NSE, Supabase — all blocked by its proxy). You're
running with real internet now, so this is the actual first live test:

1. Does `AngelSessionPool.login_all()` actually authenticate with real Angel One
   credentials? (`app/services/angel_auth.py`)
2. Does `getMarketData(mode="FULL", ...)` return the fields `market_data.py`
   assumes (`ltp`, `open`, `close`, `tradeVolume`, `symbolToken`)? These were
   inferred from Angel One's docs/forum, not a live sample response — check the
   actual JSON shape and adjust `Quote` parsing in `market_data.py` if field
   names differ.
3. Whole scan→signal→paper-trade→square-off loop under real market data timing.

Run `POST /trading/scanner/start` during market hours and work through whatever
breaks. That's expected — iterate on `market_data.py` / `angel_auth.py` as
needed based on real API responses.

## Backtest validation (why fixed sizing, not compounding)

The original notebook's portfolio simulator (`Trading/Stocks/main.ipynb`, the
cell computing "Final Balance ₹23,44,699") claimed +23,346% ROI. Walk-forward
analysis on the actual trade ledger showed:
- Win rate is genuinely stable: 58.7% first half of the year vs 57.8% second
  half — the entry signal itself may have real value.
- But the ROI number is a compounding/leverage artifact: the simulator
  reinvests the ENTIRE balance at 5x leverage every day with no cap. Average
  position size grew from ₹11K (Nov 2024) to ₹3.47M (Nov 2025) — 300x bigger
  bets, not 300x more skill. October 2025 alone produced 64% of total profit;
  top 10 trades = 32% of all winning P&L. Classic concentration/luck profile.
- No real account gets uniform 5x MIS margin on 500 stocks including illiquid
  smallcaps, and the simulator assumes zero slippage even at huge order sizes.

**Conclusion:** treat the ~58% win rate as the thing worth proving out live.
`paper_trading.py` intentionally uses fixed capital per trade, no leverage, no
compounding. Don't "fix" this back to match the notebook's simulator — that's
the mistake, not a missing feature.

## Decisions already made (don't re-litigate without reason)

- DB: Supabase (free tier) planned for hosted/production; local Postgres for
  now. Neon is also already configured in `.env` as an alternative.
- Hosting: Oracle Cloud "Always Free" ARM VM, planned for LATER — not now.
  Priority order is: backend solid locally → deploy → Claude command interface
  → Expo app. Don't jump ahead to deployment or the app before the backend
  actually works end-to-end with live data.
- Autonomy: paper trades auto-execute on signal, no confirmation needed, fully
  logged. Live trading autonomy is a future decision, after a proven paper
  track record.
- No hosted/remote Angel One MCP exists (checked npm + Anthropic's connector
  registry + web search, 2026-07-23) — both `angel-one-mcp` and
  `bhavesh0009/angel-one-mcp-server` are stdio-only, local-subprocess. Not
  worth re-investigating.

## Safety rules — do not relax these without Tadka explicitly asking

- Everything defaults to paper (`is_paper=TRUE`, `mode='PAPER'`). There is no
  flag anywhere that flips a trade to real money — going live would mean
  writing a new, separate, explicitly-reviewed order-placement path.
- `MAX_TRADES_PER_DAY`, `CAPITAL_PER_TRADE`, `MIN_GAP_PERCENT`, `VOL_SHOCK_MULT`,
  `SQUARE_OFF_TIME` are all in `app/core/config.py`, env-overridable.

## Next steps, in order

1. Get a live smoke test working: login, one bulk quote call, one candle call.
   Fix whatever the real API responses reveal is wrong in `market_data.py`.
2. Run a full scan→paper-trade→square-off cycle during market hours, check
   `portfolio_ledger` / `trades` afterward.
3. Iterate on strategy accuracy (more indicators can be added as new
   `Strategy` subclasses in `strategy_engine.py` — the framework is already
   pluggable for this).
4. Only after the above is solid: Supabase, Oracle deployment, a Claude-facing
   command API, then the Expo app.
