# setup.ps1 - Run on NEW machine as Administrator
# Restores NocoDB + OpenClaw (WhatsApp bot) + Task Scheduler tasks.
#
# Prerequisites:
#   Python, Docker Desktop, Git installed
#   UNC access to \\file-server\Shared Folder\Scripts
#
# Usage (PowerShell as Administrator):
#   powershell.exe -ExecutionPolicy Bypass -File "\\file-server\Shared Folder\Scripts\_migration\setup.ps1"

$ErrorActionPreference = "Continue"
$src   = "\\file-server\Shared Folder\Scripts\_migration"
$aibot = "D:\AI_Bot"

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  Migration Setup - new machine"                -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan

# -- check Docker --------------------------------------------------------------
Write-Host "`nChecking Docker..." -NoNewline
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host " FAIL" -ForegroundColor Red
    Write-Host "Start Docker Desktop first, then re-run this script."
    exit 1
}
Write-Host " OK" -ForegroundColor Green

# -- [1/6] Directories ---------------------------------------------------------
Write-Host "`n[1/6] Creating directory structure..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path "$aibot\openclaw"   | Out-Null
New-Item -ItemType Directory -Force -Path "$aibot\_restore_tmp" | Out-Null
Write-Host "      -> $aibot\"

# -- [2/6] OpenClaw Docker image -----------------------------------------------
Write-Host "`n[2/6] Loading OpenClaw Docker image (1-3 min)..." -ForegroundColor Yellow
Write-Host "      Copying tar locally first (network -> local)..."
Copy-Item -Force "$src\openclaw_image.tar" "$aibot\_restore_tmp\openclaw_image.tar"
docker load -i "$aibot\_restore_tmp\openclaw_image.tar"
Write-Host "      -> openclaw:local loaded"

# -- [3/6] NocoDB Docker volume ------------------------------------------------
Write-Host "`n[3/6] Restoring NocoDB data..." -ForegroundColor Yellow
Copy-Item -Force "$src\nocodb_data.tar.gz" "$aibot\_restore_tmp\nocodb_data.tar.gz"
docker volume create nocodb_data | Out-Null
# Use forward slashes for Docker volume mount path
$restore_fwd = ($aibot -replace '\\','/') + "/_restore_tmp"
docker run --rm `
    -v nocodb_data:/data `
    -v "${restore_fwd}:/backup" `
    alpine `
    tar xzf /backup/nocodb_data.tar.gz -C /data
Write-Host "      -> nocodb_data volume restored"

# -- [4/6] OpenClaw files ------------------------------------------------------
Write-Host "`n[4/6] Restoring OpenClaw data + config..." -ForegroundColor Yellow

# data/ and workspace/ - skip Linux symlinks that can't be read on Windows
robocopy "$src\openclaw\data"      "$aibot\openclaw\data"      /E /XD ".bin" /XJ /NP /NFL /NDL /R:1 /W:1
robocopy "$src\openclaw\workspace" "$aibot\openclaw\workspace" /E /XD ".bin" "venv" /XJ /NP /NFL /NDL /R:1 /W:1

Copy-Item -Force "$src\openclaw\.env"               "$aibot\openclaw\.env"
Copy-Item -Force "$src\openclaw\docker-compose.yml" "$aibot\openclaw\docker-compose.yml"

# Launcher bat files (invoked by Task Scheduler)
Copy-Item -Force "$src\openclaw\run-nocodb-briefing.bat" "$aibot\openclaw\run-nocodb-briefing.bat"
Copy-Item -Force "$src\openclaw\run-email-briefing.bat"  "$aibot\openclaw\run-email-briefing.bat"

if (Test-Path "$src\openclaw\google-ocr.json") {
    Copy-Item -Force "$src\openclaw\google-ocr.json" "$aibot\openclaw\google-ocr.json"
    New-Item -ItemType Directory -Force -Path "$aibot\openclaw\data\config" | Out-Null
    Copy-Item -Force "$src\openclaw\google-ocr.json" "$aibot\openclaw\data\config\google-ocr.json"
}
Write-Host "      -> data\  workspace\  .env  docker-compose.yml  bat files"

# -- [5/6] Start Docker containers ---------------------------------------------
Write-Host "`n[5/6] Starting Docker containers..." -ForegroundColor Yellow

# NocoDB
# NC_WEBHOOK_ALLOW_PRIVATE_NETWORK=true is required: NocoDB >= 2026.05 blocks
# webhook calls to private/LAN IPs by default (SSRF protection), which breaks
# this all-LAN deployment entirely without it.
docker rm -f nocodb 2>$null
docker run -d `
    --name nocodb `
    --restart always `
    -e NC_WEBHOOK_ALLOW_PRIVATE_NETWORK=true `
    -e NC_JWT_EXPIRES_IN=7d `
    -v nocodb_data:/usr/app/data `
    -p 8081:8080 `
    nocodb/nocodb:latest
Write-Host "      -> NocoDB  :8081"

# OpenClaw (WhatsApp bot built-in)
Set-Location "$aibot\openclaw"
docker compose up -d
Set-Location $src
Write-Host "      -> OpenClaw  :18789  (WhatsApp channel included)"

# -- [6/6] Task Scheduler tasks ------------------------------------------------
Write-Host "`n[6/6] Importing Task Scheduler tasks..." -ForegroundColor Yellow

# Tasks to skip (obsolete or machine-specific)
$skip_tasks = @("WechatDailyReminders")

$task_dir = "$src\tasks"
foreach ($f in Get-ChildItem "$task_dir\*.xml") {
    $tname = $f.BaseName

    if ($skip_tasks -contains $tname) {
        Write-Host "      -- SKIP $tname (obsolete)" -ForegroundColor DarkGray
        continue
    }

    try {
        $xml = Get-Content $f.FullName -Raw

        # Fix hardcoded drive letter: C:\AI_Bot\ -> D:\AI_Bot\ (or whatever $aibot is)
        $xml = $xml -replace [regex]::Escape("C:\AI_Bot\"), "$aibot\"

        Register-ScheduledTask -Xml $xml -TaskName $tname -Force | Out-Null
        Write-Host "      -> $tname"
    } catch {
        Write-Host "      !! $tname - $_" -ForegroundColor Red
        Write-Host "         Open Task Scheduler > right-click > Properties > re-enter credentials" -ForegroundColor DarkYellow
    }
}

# -- cleanup -------------------------------------------------------------------
Write-Host "`nCleaning up temp files..."
Remove-Item -Recurse -Force "$aibot\_restore_tmp"

# -- summary -------------------------------------------------------------------
$my_ip = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.InterfaceAlias -notmatch "Loopback|Hyper-V|WSL|vEthernet" } |
    Select-Object -First 1).IPAddress

Write-Host ""
Write-Host "================================================" -ForegroundColor Green
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Green
Write-Host ""
Write-Host "This machine's LAN IP: $my_ip" -ForegroundColor Cyan
Write-Host ""
Write-Host "Services now running:" -ForegroundColor Green
Write-Host "  NocoDB      http://localhost:8081"
Write-Host "  OpenClaw    http://localhost:18789"
Write-Host ""
Write-Host "MANUAL STEPS REMAINING:" -ForegroundColor Yellow
Write-Host ""
Write-Host "1. WhatsApp - check if session carried over:"
Write-Host "   docker logs openclaw-openclaw-gateway-1 --tail 30"
Write-Host "   Look for 'WhatsApp connected' or 'QR code' message."
Write-Host "   If QR: scan from your phone WhatsApp > Linked Devices > Link a Device"
Write-Host ""
Write-Host "2. Start Flask NocoDB API (provides data to WhatsApp bot):"
Write-Host "   cd '\\file-server\Shared Folder\Scripts'"
Write-Host "   .\_lib\server\venv\Scripts\activate"
Write-Host "   python -m _lib.server.run_server"
Write-Host "   Then add to Task Scheduler (or keep running in terminal for now)"
Write-Host ""
Write-Host "3. Task Scheduler - if any tasks failed above:"
Write-Host "   Open Task Scheduler > right-click task > Properties"
Write-Host "   Re-enter Windows user credentials (must run as a user with network access)"
Write-Host ""
Write-Host "4. Verify old machine is no longer running these services"
Write-Host "   before decommissioning it."
