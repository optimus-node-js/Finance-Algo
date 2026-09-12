# 🚀 Quick Start Guide

Get up and running in 10 minutes!

## Step 1: Install Prerequisites (5 minutes)

### Windows:
```powershell
# Install Python from: https://www.python.org/downloads/
# Install PostgreSQL from: https://www.postgresql.org/download/windows/
# Install Node.js from: https://nodejs.org/

# Verify installations
python --version
node --version
psql --version
```

### Linux/Mac:
```bash
# Install Python, PostgreSQL, Node.js using your package manager
sudo apt install python3 python3-pip postgresql nodejs npm  # Ubuntu/Debian
# or
brew install python postgresql node  # macOS

# Verify installations
python3 --version
node --version
psql --version
```

## Step 2: Install Project Dependencies (2 minutes)

```bash
# Navigate to project folder
cd Preserve_Migration

# Install Node modules
npm install

# Install Python packages
pip install -r requirements.txt

# Install Jupyter
pip install jupyter notebook
```

## Step 3: Configure Credentials (3 minutes)

### 3.1 Database Configuration

**Option A: Use Local PostgreSQL**

1. Make sure PostgreSQL is running
2. Edit `.env` file:
   ```env
   LOCAL_DB_USER=postgres
   LOCAL_DB_PASS=your_password_here
   LOCAL_DB_HOST=localhost
   LOCAL_DB_PORT=5432
   LOCAL_DB_NAME=postgres
   ```

**Option B: Use Neon Cloud (Recommended)**

1. Sign up at https://neon.tech (free)
2. Create a project and copy connection string
3. Edit `.env`:
   ```env
   NEON_DB_URL=postgresql://user:***REDACTED***@host/db?sslmode=require
   ```

### 3.2 AngelOne Configuration

1. **Get API Credentials** from https://smartapi.angelbroking.com/
2. Edit `.env` and add at least one API key:
   ```env
   ANGEL_API_KEY_1=your_api_key_here
   ANGEL_CLIENT_CODE_1=your_client_code
   ANGEL_PASSWORD_1=your_password
   ANGEL_TOTP_SECRET_1=your_totp_secret
   ```

3. **Get Cookie** (for web scraping):
   - Open https://www.angelone.in/ in browser
   - Press F12 → Network tab
   - Login
   - Copy Cookie header from any request
   - Add to `.env`:
     ```env
     ANGEL_COOKIE=your_very_long_cookie_string
     ```

## Step 4: Run the Project!

### Method 1: Jupyter Notebook (Recommended for First Time)

```bash
# Start Jupyter
jupyter notebook

# In browser, open main.ipynb
# Run cells one by one (Shift+Enter)
```

**Expected Output:**
- Cell 0: "✅ Database Connection Successful!"
- Cell 1: "Setup Complete. Uncomment the initialization line..."
- Cell 2: "🎉 SKELETON CREATED: data/NSE_STOCKS_SKELETON.json"
- ... and so on

### Method 2: FastAPI Server

```bash
# Start server
python -m uvicorn fastapi_app:app --reload

# Open browser
# Visit: http://localhost:8000/docs

# Click on: POST /seed/stocks/full-refresh
# Click "Try it out" → "Execute"
```

## Troubleshooting

### Issue: "Database Connection Failed"
```bash
# Check if PostgreSQL is running
# Windows:
Get-Service postgresql*

# Linux:
sudo systemctl status postgresql

# Mac:
brew services list
```

### Issue: "AngelOne API Error"
- **Solution 1**: Update cookie (expires frequently!)
- **Solution 2**: Verify API credentials
- **Solution 3**: Check if API access is enabled

### Issue: "Module not found"
```bash
pip install --upgrade -r requirements.txt
```

## What's Next?

✅ **Data is now in your database!**

### Query Your Data:
```sql
-- Connect to database
psql -U postgres -d postgres

-- View stock count
SELECT COUNT(*) FROM stocks;

-- View F&O stocks
SELECT symbol, company_name FROM stocks WHERE is_fno = true;

-- View Nifty 50
SELECT s.symbol, s.company_name 
FROM stocks s
JOIN stock_indices_map sim ON s.id = sim.stock_id
JOIN indices i ON sim.index_id = i.id
WHERE i.name = 'NIFTY 50';
```

### Use the API:
```bash
# Get logs
curl http://localhost:8000/logs

# Clean all data
curl -X DELETE http://localhost:8000/clean/all

# Refresh data
curl -X POST http://localhost:8000/seed/stocks/full-refresh
```

## Tips

1. **Cookie expires frequently** - Update it in `.env` when you get errors
2. **Use multiple API keys** - Helps avoid rate limits
3. **Run weekly** - Keep your data fresh
4. **Check logs** - View at http://localhost:8000/logs or in `logs/app.log`

## Need More Help?

- 📖 Read full docs: `docs/README.md`
- ✅ Check setup: `docs/SETUP_CHECKLIST.md`
- 🐛 Issues: Check `logs/app.log`

---

**Happy coding! 🎉**
