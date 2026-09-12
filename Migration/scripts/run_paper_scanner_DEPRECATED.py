# Superseded. The scanner now runs inside the FastAPI backend itself
# (app/services/scanner_scheduler.py), controlled via:
#   POST /trading/scanner/start
#   POST /trading/scanner/stop
#   GET  /trading/scanner/status
# Start the backend with: uvicorn app.main:app --reload --port 8000
# then call those endpoints. This standalone script file can be deleted.
