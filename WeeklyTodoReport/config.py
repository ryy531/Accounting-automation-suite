# WeeklyTodoReport — central constants (no credentials here)

# ── OpenClaw gateway ──────────────────────────────────────────────────────────
OPENCLAW_CONTAINER = "openclaw-openclaw-gateway-1"
ALEX_WHATSAPP_TARGET = "+15550000000"  # Alex Yu, matches the main WhatsApp DM thread

# ── Email (via himalaya inside the OpenClaw container, "ExampleCo" account) ────
HIMALAYA_BIN = "/home/node/.openclaw/bin/himalaya"
HIMALAYA_CONFIG = "/home/node/.openclaw/himalaya/config.toml"
HIMALAYA_ACCOUNT = "ExampleCo"
HIMALAYA_FROM_ADDRESS = "infosk@example-firm.com"
ALEX_EMAIL = "staff@example-firm.com"

# ── Output ────────────────────────────────────────────────────────────────────
OUTPUT_DIR = r"\\file-server\Shared Folder\4. General - Scan-Notes-Form-PPT\3. Task\Weekly Report"
RETENTION_DAYS = 35  # delete older Weekly_*.xlsx on each run

# ── Chat extraction helper (lives inside the container, alongside
#    nocodb_briefing.py / email_briefing.py) ──────────────────────────────────
CHAT_EXTRACT_SCRIPT = "/home/node/.openclaw/weekly_todo_extract.py"
CHAT_EXTRACT_OUT = "/home/node/.openclaw/weekly_todo_extract.json"
