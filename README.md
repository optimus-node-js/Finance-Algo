# Finance-Algo: NSE Paper-Trading & Momentum Scanner

> FastAPI backend that seeds the full NSE instrument universe, pulls bulk quotes from Angel One SmartAPI through a multi-key TOTP session pool, runs pluggable momentum strategies on a scheduler and auto-executes **paper trades** with tax-aware P&L, all logged to PostgreSQL (Neon + Supabase).

![Python](https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white) ![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white) ![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=flat-square&logo=postgresql&logoColor=white) ![Angel One SmartAPI](https://img.shields.io/badge/Angel%20One%20SmartAPI-E4572E?style=flat-square) ![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white) ![Vercel](https://img.shields.io/badge/Vercel-000000?style=flat-square&logo=vercel&logoColor=white)

---

## Overview

The goal: an automated algo-trading platform for Indian equities that **proves itself on paper first**. Signals auto-execute without per-trade confirmation, every order and ledger entry is persisted, and live trading is gated on a proven paper track record. A mobile (Expo) companion app is planned next.

## Features

- **NSE universe seeding**: fetch, enrich and seed instruments into Postgres (`seeder`, `nse_fetcher`, `enrichment`)
- **Angel One session pool**: TOTP-based auto-login across up to 5 API keys for higher rate limits
- **Bulk market data**: `getMarketData` quotes + historical candles
- **Strategy engine**: pluggable `Strategy` ABC; ships with `GapVolumeShockStrategy` and a momentum ranker
- **Paper-trading engine**: fixed-size orders, Indian tax/charges calculation, portfolio ledger
- **Scheduler**: APScheduler wires market data strategy paper trades during market hours
- **Multi-database**: local Postgres, Neon and Supabase engines via SQLAlchemy
- **Structured DB logging** for every operation

## API

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/trading/scanner/start` · `/stop` | Start/stop the scheduled scanner |
| `GET` | `/trading/scanner/status` | Scanner state |
| `GET` | `/trading/positions` | Open paper positions |
| `GET` | `/market/instruments/count` | Seeded instruments |
| `GET` | `/market/momentum` | Ranked momentum list |
| `GET` | `/portfolio/ledger` · `/portfolio/summary` | Paper P&L ledger and summary |
| `POST` | `/seed/...` · `/clean/...` | NSE data seeding pipeline |

## Structure

```
AlgoTrading/
├── CLAUDE.md                 # project context & verified status
└── AlgoBackend/
    ├── app/
    │   ├── main.py           # FastAPI entrypoint
    │   ├── core/             # settings, DB-backed logging
    │   ├── db/session.py     # local + Neon + Supabase engines
    │   ├── routers/          # seed, clean, logs, market, trading, portfolio, account, live_data
    │   └── services/         # angel_auth, market_data, strategy_engine, paper_trading, scanner_scheduler
    ├── sql/schema.sql        # instruments, market data, signals, orders, trades, ledger
    └── requirements.txt
Migration/                    # Dockerised migration server (Vercel-ready)
```

## Getting Started

```bash
git clone https://github.com/optimus-node-js/Finance-Algo.git
cd Finance-Algo/AlgoTrading/AlgoBackend
```

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env     # add your Angel One keys + TOTP secrets and Postgres URLs
psql "$DATABASE_URL" -f sql/schema.sql
uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs` for the interactive Swagger UI.

> Credentials previously committed have been purged from the git history. Always supply your own via `.env`.

**Predecessor:** [algoFinance](https://github.com/optimus-node-js/algoFinance) (microservice HFT platform).

---

## Author

**Rahul Vishwakarma**, Senior Full Stack Developer based in Ahmedabad, India

[![Portfolio](https://img.shields.io/badge/Portfolio-rahul--vishwakarma--portfolio.vercel.app-0A66C2?style=flat-square&logo=vercel&logoColor=white)](https://rahul-vishwakarma-portfolio.vercel.app/)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-Rahul%20Vishwakarma-0A66C2?style=flat-square&logo=linkedin&logoColor=white)](https://linkedin.com/in/rahul-vishwakarma-101346192)
[![Email](https://img.shields.io/badge/Email-karmarahul67%40gmail.com-D14836?style=flat-square&logo=gmail&logoColor=white)](mailto:karmarahul67@gmail.com)
[![GitHub](https://img.shields.io/badge/GitHub-optimus--node--js-181717?style=flat-square&logo=github)](https://github.com/optimus-node-js)

