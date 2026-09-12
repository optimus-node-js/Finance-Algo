# 📈 NSE Stock Data Migration Project

A comprehensive Python-based data migration system that fetches stock data from multiple sources (NSE, AngelOne) and populates a PostgreSQL database with enriched stock information.

## 🎯 Project Overview

This project automates the process of:
- Fetching official NSE (National Stock Exchange) equity listings
- Retrieving detailed stock information from NSE indices
- Enriching data with AngelOne API (tokens, trading info, market cap)
- Storing structured data in PostgreSQL database
- Providing a FastAPI server for programmatic access

## 📋 Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Database Setup](#database-setup)
- [Running the Project](#running-the-project)
- [API Documentation](#api-documentation)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)

## 🔧 Prerequisites

### Required Software

1. **Python 3.8+**
   - Download from: https://www.python.org/downloads/
   - Verify installation: `python --version`

2. **Node.js 14+** (for package.json scripts)
   - Download from: https://nodejs.org/
   - Verify installation: `node --version`

3. **PostgreSQL 12+**
   - Download from: https://www.postgresql.org/download/
   - Or use cloud: [Neon](https://neon.tech) (free tier available)
   - Verify installation: `psql --version`

4. **Git** (optional, for version control)
   - Download from: https://git-scm.com/

### API Accounts

1. **AngelOne Account**
   - Sign up: https://www.angelone.in/
   - Get API access: https://smartapi.angelbroking.com/
   - You'll need:
     - API Key(s)
     - Client Code
     - Password
     - TOTP Secret

## 📥 Installation

### Step 1: Clone/Download the Project

If you received this as a ZIP file, extract it. Otherwise:

```bash
git clone <repository-url>
cd Preserve_Migration
```

### Step 2: Install Node Dependencies

```bash
npm install
```

### Step 3: Install Python Dependencies

**On Windows (PowerShell):**
```powershell
pip install -r requirements.txt
```

**On Linux/Mac:**
```bash
pip3 install -r requirements.txt
```

### Step 4: Install Jupyter (for running notebooks)

```bash
pip install jupyter notebook
```

## ⚙️ Configuration

### 1. Database Configuration

#### Option A: Local PostgreSQL

1. **Install PostgreSQL** (if not already installed)

2. **Create a database** (optional, or use default 'postgres'):
   ```sql
   CREATE DATABASE your_database_name;
   ```

3. **Update `.env` file**:
   ```env
   LOCAL_DB_USER=postgres
   LOCAL_DB_PASS=your_password_here
   LOCAL_DB_HOST=localhost
   LOCAL_DB_PORT=5432
   LOCAL_DB_NAME=postgres
   ```

#### Option B: Neon Cloud Database (Recommended for Production)

1. Sign up at https://neon.tech (free tier available)
2. Create a new project
3. Copy the connection string
4. Update `.env`:
   ```env
   NEON_DB_URL=postgresql://user:***REDACTED***@host/dbname?sslmode=require
   ```

### 2. AngelOne API Configuration

#### Getting API Credentials:

1. **Sign up** at https://www.angelone.in/
2. **Register for API access** at https://smartapi.angelbroking.com/
3. **Generate API Key(s)**:
   - Go to API section
   - Create up to 5 API keys (recommended for load balancing)
   - Note down: API Key, Client Code, Password, TOTP Secret

#### Update `.env` file:

```env
# Key 1 (Required)
ANGEL_API_KEY_1=your_api_key_1_here
ANGEL_CLIENT_CODE_1=your_client_code_here
ANGEL_PASSWORD_1=your_password_here
ANGEL_TOTP_SECRET_1=your_totp_secret_here

# Keys 2-5 (Optional, for load balancing)
ANGEL_API_KEY_2=your_api_key_2_here
ANGEL_CLIENT_CODE_2=your_client_code_here
ANGEL_PASSWORD_2=your_password_here
ANGEL_TOTP_SECRET_2=your_totp_secret_here
# ... and so on
```

### 3. AngelOne Cookie Configuration (for Web Scraping)

**⚠️ Important: This cookie expires frequently and needs to be updated!**

#### How to get the cookie:

1. Open https://www.angelone.in/ in **Chrome/Firefox**
2. Press `F12` to open **Developer Tools**
3. Go to the **Network** tab
4. **Login** to your AngelOne account
5. Look for any request to `angelone.in` or `global-search`
6. Click on it and find **Request Headers**
7. Copy the entire **Cookie** value
8. Also copy:
   - `X-Deviceid` header value
   - `X-Clientpublicip` header value (your public IP)

#### Update `.env`:

```env
ANGEL_COOKIE=your_very_long_cookie_string_here
ANGEL_DEVICE_ID=your_device_id_here
ANGEL_CLIENT_IP=your_public_ip_here
```

#### OR update `main.ipynb` Cell 6:

```python
COOKIE_STRING = "your_cookie_string_here"

HEADERS_TEMPLATE = {
    # ... other headers ...
    "X-Deviceid": "your_device_id_here",
    "X-Clientpublicip": "your_public_ip_here",
    "Cookie": COOKIE_STRING
}
```

## 🗄️ Database Setup

### Option 1: Automatic Schema Creation

The application automatically creates tables when you first run it. Just make sure your database credentials are correct in `.env`.

### Option 2: Manual Schema Creation (Optional)

If you prefer to create the schema manually:

```bash
psql -U postgres -d your_database_name -f sql/schema.sql
```

**Tables Created:**
- `indices` - NSE indices information
- `stocks` - Master stock information
- `stock_indices_map` - Relationship between stocks and indices
- `system_logs` - Application logs

## 🚀 Running the Project

### Option 1: Using Jupyter Notebook (Recommended for First Run)

1. **Start Jupyter**:
   ```bash
   jupyter notebook
   ```

2. **Open** `main.ipynb` in your browser

3. **Run cells in order**:
   - **Cell 0**: Database connection test
   - **Cell 1**: Initialize AngelOne API sessions
   - **Cell 2**: Fetch NSE equity list (creates skeleton)
   - **Cell 3**: Fetch NSE indices list
   - **Cell 4**: Fetch detailed index constituents
   - **Cell 5**: Enrich with index relationships
   - **Cell 6**: Fetch tokens from AngelOne
   - **Cell 7**: Seed database with final data

4. **Monitor progress**: Each cell shows progress bars and completion status

### Option 2: Using FastAPI Server

1. **Start the server**:
   ```bash
   python -m uvicorn fastapi_app:app --reload
   ```

2. **Access the API**:
   - API Docs: http://localhost:8000/docs
   - System Logs: http://localhost:8000/logs

3. **Trigger data refresh**:
   - Go to http://localhost:8000/docs
   - Try the `/seed/stocks/full-refresh` endpoint

### Option 3: Using Docker (Production)

1. **Build the image**:
   ```bash
   docker-compose build
   ```

2. **Start the container**:
   ```bash
   docker-compose up -d
   ```

3. **View logs**:
   ```bash
   docker-compose logs -f
   ```

## 📚 API Documentation

### Available Endpoints

#### Health Check
- **GET** `/` - Check if API is running

#### Seeding Endpoints
- **POST** `/seed/indices` - Fetch and seed NSE indices
- **POST** `/seed/stocks/full-refresh` - Complete data refresh pipeline
- **POST** `/seed/stocks/quick-update` - Quick DB update from existing JSON

#### Cleaning Endpoints
- **DELETE** `/clean/indices` - Clear indices table
- **DELETE** `/clean/stocks` - Clear stocks table
- **DELETE** `/clean/all` - Clear all tables
- **DELETE** `/cleanup` - Delete generated files (logs, JSONs)

#### Monitoring
- **GET** `/logs?limit=200` - View system logs (beautiful UI)

### Example: Full Refresh via API

```bash
curl -X POST "http://localhost:8000/seed/stocks/full-refresh?createJSONFiles=true"
```

## 📁 Project Structure

```
Preserve_Migration/
├── app/                    # FastAPI application (modular)
│   ├── core/              # Core configurations
│   │   ├── config.py      # Environment settings
│   │   └── logging.py     # Logging utilities
│   ├── db/                # Database connections
│   │   └── session.py     # DB engine management
│   ├── routers/           # API route handlers
│   │   ├── seed.py        # Seeding endpoints
│   │   ├── clean.py       # Cleanup endpoints
│   │   └── logs.py        # Logging endpoints
│   ├── services/          # Business logic
│   │   ├── nse_fetcher.py # NSE data fetching
│   │   ├── enrichment.py  # Data enrichment
│   │   └── seeder.py      # Database seeding
│   └── main.py            # FastAPI app entry point
│
├── data/                  # Generated data files
│   ├── indices/           # Individual index JSON files
│   └── NSE_*.json         # Master stock data
│
├── logs/                  # Application logs
│   ├── app.log            # Main application log
│   └── angel_missing_stocks.log  # Missing stocks log
│
├── sql/                   # Database schemas
│   └── schema.sql         # PostgreSQL schema
│
├── main.ipynb             # Jupyter notebook (main workflow)
├── fastapi_app.py         # FastAPI app (monolithic version)
├── requirements.txt       # Python dependencies
├── package.json           # Node.js scripts
├── docker-compose.yml     # Docker configuration
├── Dockerfile             # Docker image definition
├── .env                   # Environment variables (EDIT THIS!)
├── .env.example           # Environment template
└── README.md              # This file
```

## 🔍 Data Flow

```
1. NSE Website → CSV Download → NSE_STOCKS_SKELETON.json
                                        ↓
2. NSE API → Indices List → NSE_Indices_List.json
                                        ↓
3. NSE API → Index Details → data/indices/*.json
                                        ↓
4. Local Processing → Enrich with Indices Data
                                        ↓
5. AngelOne API → Token Hunt → Final Enriched Data
                                        ↓
6. PostgreSQL Database → stocks, indices, stock_indices_map tables
```

## ⏱️ Execution Time

- **Full Refresh**: ~10-15 minutes (for ~2200 stocks)
- **Indices Fetch**: ~2-3 minutes
- **Token Hunt**: ~5-8 minutes (with 5 API keys)

## 🐛 Troubleshooting

### Issue: Database Connection Failed

**Solution:**
1. Check if PostgreSQL is running:
   ```bash
   # Windows
   Get-Service postgresql*
   
   # Linux/Mac
   sudo systemctl status postgresql
   ```

2. Verify credentials in `.env`
3. Test connection manually:
   ```bash
   psql -U postgres -h localhost
   ```

### Issue: AngelOne API Returns 401/403

**Possible Causes:**
- Cookie expired (most common)
- Device ID mismatch
- IP address changed

**Solution:**
1. Get fresh cookie from browser (see Configuration section)
2. Update `X-Deviceid` and `X-Clientpublicip` headers
3. Restart the application

### Issue: Import Error / Module Not Found

**Solution:**
```bash
pip install --upgrade -r requirements.txt
```

### Issue: Jupyter Kernel Dead

**Solution:**
1. Restart Jupyter:
   ```bash
   jupyter notebook
   ```
2. Clear outputs: `Cell > All Output > Clear`
3. Restart kernel: `Kernel > Restart & Clear Output`

### Issue: Rate Limited by NSE/AngelOne

**Solution:**
- NSE: Wait 5-10 minutes and retry
- AngelOne: Add more API keys to CREDENTIALS_POOL
- Reduce MAX_WORKERS in concurrent fetch functions

## 📊 Database Queries Examples

### Get all F&O stocks:
```sql
SELECT symbol, company_name, sector 
FROM stocks 
WHERE is_fno = true;
```

### Get Nifty 50 constituents:
```sql
SELECT s.symbol, s.company_name, s.market_cap_value
FROM stocks s
JOIN stock_indices_map sim ON s.id = sim.stock_id
JOIN indices i ON sim.index_id = i.id
WHERE i.name = 'NIFTY 50'
ORDER BY s.market_cap_value DESC;
```

### Get stocks by sector:
```sql
SELECT sector, COUNT(*) as count
FROM stocks
GROUP BY sector
ORDER BY count DESC;
```

## 🔐 Security Notes

- **Never commit `.env` file** to version control
- `.env` is already in `.gitignore`
- Keep your API keys and cookies private
- Cookies expire frequently - update regularly
- Use environment variables in production

## 🤝 Contributing

If you're sharing this project:
1. Make sure `.env` contains only template values
2. Remove any personal logs or data files
3. Clear Jupyter notebook outputs: `Cell > All Output > Clear`

## 📝 License

[Your License Here]

## 🙏 Acknowledgments

- **NSE India** for public stock data
- **AngelOne** for API access
- **Neon** for cloud PostgreSQL hosting

## 📞 Support

For issues or questions:
1. Check [Troubleshooting](#troubleshooting) section
2. Review logs in `logs/app.log`
3. Check AngelOne API documentation: https://smartapi.angelbroking.com/docs

---

**Happy Stock Data Mining! 📈**
