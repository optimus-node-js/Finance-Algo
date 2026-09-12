# ✅ Setup Checklist

Use this checklist to ensure you've configured everything correctly before running the project.

## 📋 Pre-Installation Checklist

- [ ] Python 3.8+ installed (`python --version`)
- [ ] Node.js 14+ installed (`node --version`)
- [ ] PostgreSQL 12+ installed and running
- [ ] Git installed (optional)

## 🔐 Credentials Checklist

### Database Credentials
- [ ] PostgreSQL is running
- [ ] Created a database (or using default 'postgres')
- [ ] Know your PostgreSQL username
- [ ] Know your PostgreSQL password
- [ ] Updated `.env` with database credentials

### AngelOne API Credentials
- [ ] Created AngelOne account (https://www.angelone.in/)
- [ ] Registered for API access (https://smartapi.angelbroking.com/)
- [ ] Generated at least 1 API key
- [ ] Have your Client Code
- [ ] Have your Password
- [ ] Have your TOTP Secret
- [ ] Updated `.env` with AngelOne credentials

### AngelOne Cookie (for Web Scraping)
- [ ] Logged into AngelOne web app
- [ ] Opened Browser DevTools (F12)
- [ ] Captured Cookie from Network tab
- [ ] Captured X-Deviceid header
- [ ] Captured X-Clientpublicip header
- [ ] Updated `.env` or `main.ipynb` Cell 6 with these values

## 📦 Installation Checklist

- [ ] Installed Node modules: `npm install`
- [ ] Installed Python packages: `pip install -r requirements.txt`
- [ ] Installed Jupyter: `pip install jupyter notebook`
- [ ] Copied `.env.example` to `.env` and filled in values

## 🗄️ Database Checklist

- [ ] PostgreSQL service is running
- [ ] Can connect to database using credentials
- [ ] Database name matches `.env` configuration
- [ ] `.env` file has correct database URL

## 🧪 Testing Checklist

### Test 1: Database Connection
```bash
python -c "from sqlalchemy import create_engine; import os; from dotenv import load_dotenv; load_dotenv(); engine = create_engine('postgresql://postgres:***REDACTED***@localhost:5432/postgres'); conn = engine.connect(); print('✅ Database Connected!')"
```
- [ ] Database connection successful

### Test 2: Jupyter Notebook
```bash
jupyter notebook
```
- [ ] Jupyter opens in browser
- [ ] Can open `main.ipynb`
- [ ] Cell 0 runs without errors (database test)
- [ ] Cell 1 runs without errors (AngelOne API test)

### Test 3: FastAPI Server
```bash
python -m uvicorn fastapi_app:app --reload
```
- [ ] Server starts without errors
- [ ] Can access http://localhost:8000
- [ ] Can access http://localhost:8000/docs

## 🚀 First Run Checklist

### Option 1: Jupyter Notebook
- [ ] Run Cell 0 - Database connection ✅
- [ ] Run Cell 1 - AngelOne API sessions ✅
- [ ] Run Cell 2 - NSE equity list (creates ~2200 stocks) ✅
- [ ] Run Cell 3 - NSE indices list (creates ~133 indices) ✅
- [ ] Run Cell 4 - Fetch index details (creates ~133 JSON files) ✅
- [ ] Run Cell 5 - Enrich with indices data ✅
- [ ] Run Cell 6 - Fetch tokens (takes 5-10 mins) ✅
- [ ] Run Cell 7 - Seed database ✅

### Option 2: FastAPI
- [ ] Start server: `python -m uvicorn fastapi_app:app --reload`
- [ ] Go to http://localhost:8000/docs
- [ ] Run POST `/seed/stocks/full-refresh`
- [ ] Monitor progress in logs: http://localhost:8000/logs

## ✅ Verification Checklist

### Database Tables Created
```sql
-- Run these queries to verify data
SELECT COUNT(*) FROM indices;        -- Should be ~133
SELECT COUNT(*) FROM stocks;         -- Should be ~2200
SELECT COUNT(*) FROM stock_indices_map;  -- Should be ~several thousand
```

- [ ] `indices` table has ~133 records
- [ ] `stocks` table has ~2200 records
- [ ] `stock_indices_map` table has data
- [ ] `system_logs` table exists

### Files Created
- [ ] `data/NSE_STOCKS_SKELETON.json` exists
- [ ] `data/NSE_Indices_List.json` exists
- [ ] `data/indices/*.json` files exist (~133 files)
- [ ] `logs/app.log` exists

## 🐛 Troubleshooting

### If Cell 0 Fails (Database)
1. Check PostgreSQL is running
2. Verify credentials in `.env` or `main.ipynb`
3. Test connection manually: `psql -U postgres -h localhost`

### If Cell 1 Fails (AngelOne API)
1. Verify API credentials are correct
2. Check if TOTP secret is valid
3. Ensure you have API access enabled in AngelOne

### If Cell 6 Fails (Token Hunt)
1. Update cookie - it expires frequently!
2. Update X-Deviceid and X-Clientpublicip
3. Check if you're rate-limited (wait 10 mins)

### If Database Seed Fails
1. Ensure indices table is populated first (Cell 3)
2. Check database permissions
3. Review `logs/app.log` for errors

## 📝 Notes

- **Cookie Expiry**: The AngelOne cookie expires frequently (every few hours). You'll need to update it regularly.
- **Rate Limits**: NSE and AngelOne have rate limits. If you get errors, wait 5-10 minutes.
- **Data Refresh**: Run the full pipeline weekly to keep data up-to-date.
- **API Keys**: Using multiple API keys helps avoid rate limits.

## 🎉 Success!

If all checkboxes are ticked, you're ready to use the project! 

### What's Next?
- Query your database for stock insights
- Use the FastAPI endpoints to automate updates
- Schedule periodic refreshes using cron/Task Scheduler
- Build your own applications on top of this data

---

**Need Help?** Check `docs/README.md` for detailed documentation.
