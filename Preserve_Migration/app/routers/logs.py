import json
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from sqlalchemy import text, inspect
from app.db import get_db_engines

router = APIRouter(tags=["Logs"])

@router.get("/logs", response_class=HTMLResponse)
def view_logs(limit: int = 200):
    """Fetches latest system logs and renders them in a beautiful UI."""
    engines = get_db_engines()
    engine = engines.get("neon") or engines.get("local")
    if not engine:
        return HTMLResponse("<h1>❌ No Database Connection Available</h1>", status_code=500)

    try:
        with engine.connect() as conn:
            # Check if table exists first
            inspector = inspect(engine)
            if not inspector.has_table("system_logs"):
                return HTMLResponse("<h1>⚠️ 'system_logs' table does not exist yet.</h1><p>Wait for server startup or run a search to generate logs.</p>")
                
            result = conn.execute(text("SELECT * FROM system_logs ORDER BY timestamp DESC LIMIT :limit"), {"limit": limit})
            logs = result.fetchall()
    except Exception as e:
        return HTMLResponse(f"<h1>❌ Database Error</h1><p>{e}</p>", status_code=500)

    # --- HTML TEMPLATE ---
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>System Logs | Finance Server</title>
        <style>
            :root {{
                --bg-color: #0f172a;
                --card-bg: #1e293b;
                --text-primary: #f1f5f9;
                --text-secondary: #94a3b8;
                --border-color: #334155;
                --accent: #3b82f6;
                --success: #22c55e;
                --warning: #eab308;
                --error: #ef4444;
            }}
            body {{
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
                background-color: var(--bg-color);
                color: var(--text-primary);
                margin: 0;
                padding: 20px;
            }}
            .container {{
                max-width: 1400px;
                margin: 0 auto;
            }}
            header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 20px;
                padding-bottom: 20px;
                border-bottom: 1px solid var(--border-color);
            }}
            h1 {{ margin: 0; font-size: 1.5rem; }}
            .refresh-btn {{
                background-color: var(--accent);
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 6px;
                cursor: pointer;
                font-weight: 500;
                text-decoration: none;
                transition: opacity 0.2s;
            }}
            .refresh-btn:hover {{ opacity: 0.9; }}
            
            .table-container {{
                background-color: var(--card-bg);
                border-radius: 8px;
                border: 1px solid var(--border-color);
                overflow-x: auto;
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 0.875rem;
            }}
            th, td {{
                padding: 12px 16px;
                text-align: left;
                border-bottom: 1px solid var(--border-color);
            }}
            th {{
                background-color: #0f172a;
                color: var(--text-secondary);
                font-weight: 600;
                position: sticky;
                top: 0;
            }}
            tr:last-child td {{ border-bottom: none; }}
            tr:hover {{ background-color: rgba(255,255,255,0.02); }}
            
            /* Badges */
            .badge {{
                padding: 2px 8px;
                border-radius: 9999px;
                font-size: 0.75rem;
                font-weight: 600;
            }}
            .badge-INFO {{ background-color: rgba(59, 130, 246, 0.1); color: var(--accent); }}
            .badge-WARNING {{ background-color: rgba(234, 179, 8, 0.1); color: var(--warning); }}
            .badge-ERROR {{ background-color: rgba(239, 68, 68, 0.1); color: var(--error); }}
            
            .timestamp {{ color: var(--text-secondary); white-space: nowrap; font-family: monospace; }}
            .source {{ font-weight: 500; color: #e2e8f0; }}
            .event {{ color: var(--text-secondary); }}
            .context {{ 
                font-family: monospace; 
                color: var(--text-secondary); 
                font-size: 0.75rem; 
                max-width: 300px; 
                overflow: hidden; 
                text-overflow: ellipsis; 
                white-space: nowrap;
            }}
            .context:hover {{
                white-space: pre-wrap;
                max-width: none;
                word-break: break-all;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <div>
                    <h1>System Logs</h1>
                    <div style="color: var(--text-secondary); font-size: 0.875rem; margin-top: 4px;">
                        Showing latest {len(logs)} entries from <strong>{engine.url.database}</strong>
                    </div>
                </div>
                <a href="/logs?limit={limit}" class="refresh-btn">🔄 Refresh</a>
            </header>
            
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th width="180">Timestamp</th>
                            <th width="80">Level</th>
                            <th width="120">Source</th>
                            <th width="120">Event</th>
                            <th>Message</th>
                            <th width="250">Context</th>
                        </tr>
                    </thead>
                    <tbody>
    """
    
    for log in logs:
        try:
            ts = log.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            lvl = log.level
            src = log.source
            evt = log.event
            msg = log.message
            ctx = json.dumps(log.context) if log.context else ""
        except:
            ts = str(log[1])
            lvl = log[2]
            src = log[3]
            evt = log[4]
            msg = log[5]
            ctx = str(log[6]) if log[6] else ""

        html_content += f"""
                        <tr>
                            <td class="timestamp">{ts}</td>
                            <td><span class="badge badge-{lvl}">{lvl}</span></td>
                            <td class="source">{src}</td>
                            <td class="event">{evt}</td>
                            <td>{msg}</td>
                            <td class="context" title='{ctx}'>{ctx}</td>
                        </tr>
        """

    html_content += """
                    </tbody>
                </table>
            </div>
        </div>
    </body>
    </html>
    """
    return html_content
