# AlgoTrading — Project Context for Claude Code

Read this file first. It's the single source of truth for what this project is,
what's built, what's verified, and what's left. Written 2026-07-23 by a Cowork
session that built the backend but could not run it live (see "Network" below) —
that's now your job, since you're running with real internet on Tadka's machine.

**Updated 2026-09-13** after a live session on Tadka's machine: Angel One auth,
NSE seeding, and Neon+Supabase are now all confirmed working end-to-end (see
"What's DONE and verified" and "Cloud container quick-start" below). `.env`
(with real credentials) is committed to git on Tadka's explicit instruction —
he plans to revoke/rotate everything later. Treat every credential in this
repo's `.env` files as already-committed, not secret.

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
│       │   │   └── session.py          SQLAlchemy engines (local + Neon + Supabase) +
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
└── Migration/                  SUPERSEDED as the working location, but as of 2026-09-13 its code has
                                 been re-synced to mirror AlgoBackend (same app/, sql/schema.sql,
                                 requirements.txt — its own .env/data/logs are untouched). Still: work
                                 only in AlgoBackend going forward. Tadka can delete Migration/ once
                                 he's fully confirmed AlgoBackend works.
```

## How to run it (local, with a local Postgres)

```bash
cd "Finance Server/AlgoTrading/AlgoBackend"
python -m venv .venv && ./.venv/Scripts/pip install -r requirements.txt   # Windows

# one-time: create the DB and load the schema
createdb algotrading      # or use LOCAL_DB_NAME from .env if different
psql -U postgres -d algotrading -f sql/schema.sql

# start the backend
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uvicorn app.main:app --reload --port 8000
# PYTHONIOENCODING=utf-8 is required on Windows -- log_message() prints emoji
# and the default cp1252 console encoding crashes on them otherwise.

# seed the full NSE stock/index universe (network, ~2-5 min)
curl -X POST http://localhost:8000/seed/stocks/full-refresh
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
code ***REDACTED***) and DB config (`LOCAL_DB_*`, `NEON_DB_URL`, `SUPABASE_DB_URL`).
Nothing else to fill in to do a first run.

## Cloud container quick-start (no local Postgres needed)

A fresh cloud container has no local Postgres, but `.env` is committed to git
(Tadka's explicit call — see the note at the top of this file) with working
`NEON_DB_URL` and `SUPABASE_DB_URL`, so the app runs immediately against those
two without any local DB setup:

```bash
cd "Finance Server/AlgoTrading/AlgoBackend"   # or Migration/, same code
python -m venv .venv && ./.venv/Scripts/pip install -r requirements.txt
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uvicorn app.main:app --reload --port 8000
```

`get_db_engines()` in `app/db/session.py` tries local/Neon/Supabase
independently and just logs+skips whichever fails to connect (a missing local
Postgres in the container will show `[LOCAL] Connection Failed` in the startup
log — that's expected and harmless, Neon+Supabase still connect fine). Every
`/seed/*` endpoint and `seeder.py`/`nse_fetcher.py` loop over all connected
engines generically, so hitting `/seed/stocks/full-refresh` seeds whichever of
local/Neon/Supabase are reachable — no per-DB code changes needed either way.
Neon and Supabase already have the current schema applied (as of 2026-09-13:
2,305 instruments, 139 indices in both, plus local `algotrading`) — no
`psql -f sql/schema.sql` step needed unless the schema itself changes.

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
- **(2026-09-13, live-verified)** Angel One login works for real: `GET
  /account/profile` returns a live profile for client ***REDACTED*** using key 1's
  rotation pool. Keys 2-5 log in fine but fail with AG8004 on data/account
  calls — Angel One only allows 1 registered app per public IP, and only key 1
  is registered under that static-IP app. Re-enabling keys 2-5 needs separate
  public IPs (see comments in `.env`).
- **(2026-09-13, live-verified)** `/seed/stocks/full-refresh` runs clean
  end-to-end: 2,568 NSE mainboard equities fetched from
  `archives.nseindia.com/content/equities/EQUITY_L.csv` (static, unauthenticated,
  never blocked — unlike `www.nseindia.com`'s dynamic API, which 403s
  non-browser requests, confirmed live), sector data for ~1,000 of them from two
  more static NSE archive CSVs, and Angel One's public
  `OpenAPIScripMaster.json` (no auth, no rate limit) matched tokens for
  2,305/2,568 — seeded into `instruments`/`indices` in local, Neon, AND
  Supabase identically (2,305 instruments / 139 indices in each, verified by
  direct query). `index_constituents` links are still 0 everywhere — nothing
  in the current pipeline populates that table yet (open item).
- **(2026-09-13)** Third DB target added: **Supabase**, via `SUPABASE_DB_URL`
  in `.env` and a `supabase` engine in `app/db/session.py` alongside
  local/Neon. All seed/fetch code loops over `engines.items()` generically, so
  this needed no changes to `seeder.py`/`nse_fetcher.py` — just the new engine.
- **(2026-09-13)** Fixed a real bug in `nse_fetcher.py`'s (unused, dead-code)
  `fetch_nse_indices_details`: it cold-started a brand new NSE session instead
  of reusing one — harmless now since the seed pipeline doesn't call it (see
  `fetch_sector_classification` instead), fixed anyway via a shared, retrying
  `_warm_nse_session()` helper, in case it's revived later.

## What's NOT verified (do this first)

Angel One login and NSE seeding are now confirmed live (see above). Still
untested:

1. Does `getMarketData(mode="FULL", ...)` return the fields `market_data.py`
   assumes (`ltp`, `open`, `close`, `tradeVolume`, `symbolToken`)? These were
   inferred from Angel One's docs/forum, not a live sample response — check the
   actual JSON shape and adjust `Quote` parsing in `market_data.py` if field
   names differ.
2. Whole scan→signal→paper-trade→square-off loop under real market data timing.
3. **NSE SME/Emerge platform + BSE are not covered by the current stock
   universe at all.** `EQUITY_L.csv` only covers NSE mainboard (2,568 of NSE's
   ~2,867-2,978 total incl. SME). The obvious static-file candidate for SME
   (`archives.nseindia.com/content/equities/SME_EQUITY_L.csv`) was checked live
   2026-09-13 and is a stale placeholder stub, not real data — a truthful SME
   source is still unsolved. BSE (~5,900 listed companies, an entirely
   separate universe from NSE) hasn't been investigated at all yet. Both are
   open items for the "keep the stock table current via cron" task.

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

- DB: **Supabase is now live** (as of 2026-09-13), not just planned — seeded
  and verified alongside local Postgres and Neon (all 3 kept in sync by the
  seed pipeline). Local Postgres remains fine for day-to-day local dev; Neon
  and Supabase are both real, reachable cloud targets already, useful
  specifically for a cloud container that has no local Postgres.
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
