$ErrorActionPreference = "Continue"
$src    = "\\file-server\Shared Folder\Scripts\_migration"
$aibot  = "D:\AI_Bot"

# [3/7] NocoDB volume restore
Write-Host "`n[3/7] Restoring NocoDB data..." -ForegroundColor Yellow
docker volume create nocodb_data | Out-Null
docker run --rm -v nocodb_data:/data -v "D:/AI_Bot/_restore_tmp:/backup" alpine tar xzf /backup/nocodb_data.tar.gz -C /data
Write-Host "      -> nocodb_data volume restored"

# [4/7] OpenClaw config + workspace
Write-Host "`n[4/7] Restoring OpenClaw data..." -ForegroundColor Yellow
Copy-Item -Recurse -Force "$src\openclaw\data"              "$aibot\openclaw\data"
Copy-Item -Recurse -Force "$src\openclaw\workspace"         "$aibot\openclaw\workspace"
Copy-Item -Force          "$src\openclaw\.env"              "$aibot\openclaw\.env"
Copy-Item -Force          "$src\openclaw\docker-compose.yml" "$aibot\openclaw\docker-compose.yml"
if (Test-Path "$src\openclaw\google-ocr.json") {
    Copy-Item -Force "$src\openclaw\google-ocr.json" "$aibot\openclaw\google-ocr.json"
    New-Item -ItemType Directory -Force -Path "$aibot\openclaw\data\config" | Out-Null
    Copy-Item -Force "$src\openclaw\google-ocr.json" "$aibot\openclaw\data\config\google-ocr.json"
}
Write-Host "      -> openclaw\data  workspace  .env  docker-compose.yml"

# [5/7] WeChat GeweChat bot
Write-Host "`n[5/7] Restoring WeChat GeweChat bot..." -ForegroundColor Yellow
Copy-Item -Recurse -Force "$src\gewechat-channel\*" "$aibot\openclaw-wechat-channel\gewechat-channel\"
Copy-Item -Force "$src\start-wechat-gewechat-channel.bat" "$aibot\openclaw-wechat-channel\"
Copy-Item -Force "$src\gewechat-requirements.txt" "$aibot\openclaw-wechat-channel\gewechat-requirements.txt"
Write-Host "      Creating Python venv (venv312)..."
python -m venv "$aibot\openclaw-wechat-channel\venv312"
& "$aibot\openclaw-wechat-channel\venv312\Scripts\pip.exe" install --quiet requests pyyaml websockets pywin32 pyautogui pillow
Write-Host "      -> venv312 created + deps installed"
Copy-Item -Recurse -Force "$src\wechat-bot\*" "$aibot\wechat-bot\"
Write-Host "      -> wechat-bot\"

# [6/7] Start Docker containers
Write-Host "`n[6/7] Starting Docker containers..." -ForegroundColor Yellow
docker rm -f nocodb 2>$null
docker run -d --name nocodb --restart always -v nocodb_data:/usr/app/data -p 8081:8080 nocodb/nocodb:latest
Write-Host "      -> NocoDB  :8081"
Set-Location "$aibot\openclaw"
docker compose up -d
Set-Location $src
Write-Host "      -> OpenClaw  :18789"
docker rm -f gewe 2>$null
docker run -itd --name gewe --restart always -v "D:/gewe/temp:/root/temp" -p 2531:2531 -p 2532:2532 --privileged=true registry.cn-hangzhou.aliyuncs.com/gewe/gewe:latest /usr/sbin/init
Write-Host "      -> gewe WeChat bridge  :2531  (login required - see manual steps below)"

# [7/7] Task Scheduler import
Write-Host "`n[7/7] Importing Task Scheduler tasks..." -ForegroundColor Yellow
$task_dir = "$src\tasks"
foreach ($f in Get-ChildItem "$task_dir\*.xml") {
    $tname = $f.BaseName
    try {
        $xml = Get-Content $f.FullName -Raw
        Register-ScheduledTask -Xml $xml -TaskName $tname -Force | Out-Null
        Write-Host "      -> $tname"
    } catch {
        Write-Host "      !! $tname - $_" -ForegroundColor Red
    }
}

# cleanup
Write-Host "`nCleaning up temp restore files..."
Remove-Item -Recurse -Force "D:\AI_Bot\_restore_tmp"

$my_ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notmatch "Loopback|Hyper-V|WSL|vEthernet" } | Select-Object -First 1).IPAddress
Write-Host ""
Write-Host "Setup complete. LAN IP: $my_ip" -ForegroundColor Green
