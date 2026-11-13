# pack.ps1 — Run on CURRENT machine as Administrator
# Packages everything needed for migration into the network share.
#
# Usage:  cd "\\file-server\Shared Folder\Scripts\_migration"
#         .\pack.ps1

$ErrorActionPreference = "Stop"
$local  = "C:\AI_Bot\_migration_pack"      # local staging (Docker needs a local path)
$net    = "\\file-server\Shared Folder\Scripts\_migration"

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  Migration Pack — current machine"             -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan

# ── prep ────────────────────────────────────────────────────────────────────
New-Item -ItemType Directory -Force -Path $local            | Out-Null
New-Item -ItemType Directory -Force -Path "$local\tasks"    | Out-Null
New-Item -ItemType Directory -Force -Path "$local\openclaw" | Out-Null

# ── 1. OpenClaw Docker image ─────────────────────────────────────────────────
Write-Host "`n[1/6] Exporting OpenClaw Docker image (may take 1-3 min)..." -ForegroundColor Yellow
docker save openclaw:local -o "$local\openclaw_image.tar"
Write-Host "      -> openclaw_image.tar  ($([int]((Get-Item "$local\openclaw_image.tar").Length/1MB)) MB)"

# ── 2. NocoDB Docker volume ──────────────────────────────────────────────────
Write-Host "`n[2/6] Backing up NocoDB volume..." -ForegroundColor Yellow
docker run --rm `
    -v nocodb_data:/data `
    -v "${local}:/backup" `
    alpine `
    tar czf /backup/nocodb_data.tar.gz -C /data .
Write-Host "      -> nocodb_data.tar.gz  ($([int]((Get-Item "$local\nocodb_data.tar.gz").Length/1MB)) MB)"

# ── 3. OpenClaw config + workspace + env ────────────────────────────────────
Write-Host "`n[3/6] Copying OpenClaw data..." -ForegroundColor Yellow
Copy-Item -Recurse -Force "C:\AI_Bot\openclaw\data"           "$local\openclaw\data"
Copy-Item -Recurse -Force "C:\AI_Bot\openclaw\workspace"      "$local\openclaw\workspace"
Copy-Item -Force          "C:\AI_Bot\openclaw\.env"           "$local\openclaw\.env"
Copy-Item -Force          "C:\AI_Bot\openclaw\docker-compose.yml" "$local\openclaw\docker-compose.yml"
# Google OCR credentials (root-level copy, ensure it's in data/config too)
if (Test-Path "C:\AI_Bot\openclaw\google-ocr.json") {
    Copy-Item -Force "C:\AI_Bot\openclaw\google-ocr.json" "$local\openclaw\google-ocr.json"
}
Write-Host "      -> openclaw\data  workspace  .env  docker-compose.yml"

# ── 4. WeChat GeweChat bot (code only, no venv) ──────────────────────────────
Write-Host "`n[4/6] Copying WeChat GeweChat bot..." -ForegroundColor Yellow
$wx_dest = "$local\gewechat-channel"
New-Item -ItemType Directory -Force -Path $wx_dest | Out-Null
Get-ChildItem "C:\AI_Bot\openclaw-wechat-channel\gewechat-channel" |
    Where-Object { $_.Name -notin @("venv312","__pycache__",".pytest_cache") } |
    ForEach-Object { Copy-Item -Recurse -Force $_.FullName "$wx_dest\" }
Copy-Item -Force "C:\AI_Bot\openclaw-wechat-channel\start-wechat-gewechat-channel.bat" "$local\"
# Also copy requirements from wxauto-channel (same deps)
Copy-Item -Force "C:\AI_Bot\openclaw-wechat-channel\wxauto-channel\requirements.txt" "$local\gewechat-requirements.txt"
Write-Host "      -> gewechat-channel\  start-wechat-gewechat-channel.bat"

# ── 5. wechat-bot daily sender ───────────────────────────────────────────────
Write-Host "`n[5/6] Copying wechat-bot..." -ForegroundColor Yellow
Copy-Item -Recurse -Force "C:\AI_Bot\wechat-bot" "$local\wechat-bot"
Write-Host "      -> wechat-bot\"

# ── 6. Task Scheduler tasks ──────────────────────────────────────────────────
Write-Host "`n[6/6] Exporting Task Scheduler tasks..." -ForegroundColor Yellow
$tasks = @(
    "ExampleCo Payroll Prepare NocoDB",
    "NocoDB-Daily-Briefing",
    "NocoDB-Monthly-Briefing",
    "NocoDB-Weekly-Briefing",
    "OpenClaw-Email-Briefing",
    "Payroll Cron Daily",
    "WechatDailyReminders"
)
foreach ($t in $tasks) {
    try {
        $xml = Export-ScheduledTask -TaskName $t -ErrorAction Stop
        $xml | Out-File -FilePath "$local\tasks\$($t -replace '[/\\:*?\"<>|]','_').xml" -Encoding UTF8
        Write-Host "      -> $t"
    } catch {
        Write-Host "      !! SKIP '$t': $_" -ForegroundColor Red
    }
}

# ── Copy everything to network share ─────────────────────────────────────────
Write-Host "`nCopying to network share (this may take a few minutes for the Docker image)..." -ForegroundColor Yellow
robocopy $local $net /E /NP /NFL /NDL /R:2 /W:3
$rc = $LASTEXITCODE
if ($rc -ge 8) { Write-Host "  !! robocopy error: $rc" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "================================================" -ForegroundColor Green
Write-Host "  Pack complete!" -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Files saved to:"
Write-Host "  $net"
Write-Host ""
Write-Host "On the new machine, run (as Administrator):"
Write-Host "  \\file-server\Shared Folder\Scripts\_migration\setup.ps1"
