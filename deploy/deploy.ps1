<#
.SYNOPSIS
  Move LifeBots from this PC to a server (Docker) in one command.

.EXAMPLE
  .\deploy\deploy.ps1 -Server ubuntu@203.0.113.10
  .\deploy\deploy.ps1 -Server root@myvps -Port 2222 -InstallDocker
  .\deploy\deploy.ps1 -Server ubuntu@host -Key C:\Users\me\.ssh\id_ed25519

.NOTES
  Order matters (two places polling one bot token fight each other and send duplicates):
    1. stop + disable the local bots   2. copy project, .env and state.json
    3. start on the server             4. if anything fails -> local bots are restarted.
  Needs OpenSSH client (ssh, scp) and tar - both ship with Windows 10/11.
  The server needs Docker + Compose (add -InstallDocker for Ubuntu/Debian).
#>
param(
    [Parameter(Mandatory)] [string] $Server,
    [int] $Port = 22,
    [string] $Key,
    [string] $RemoteDir = "lifebots",
    [switch] $InstallDocker,
    [switch] $KeepLocalRunning
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$sshArgs = @("-p", $Port, "-o", "StrictHostKeyChecking=accept-new")
$scpArgs = @("-P", $Port, "-o", "StrictHostKeyChecking=accept-new")
if ($Key) { $sshArgs += @("-i", $Key); $scpArgs += @("-i", $Key) }

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Remote([string]$cmd) {
    & ssh @sshArgs $Server $cmd
    if ($LASTEXITCODE -ne 0) { throw "remote command failed: $cmd" }
}
function StopLocal {
    Stop-ScheduledTask -TaskName "LifeBots" -ErrorAction SilentlyContinue
    Disable-ScheduledTask -TaskName "LifeBots" -ErrorAction SilentlyContinue | Out-Null
    Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -match [regex]::Escape($root) -and $_.Name -match "python" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
function StartLocal {
    Enable-ScheduledTask -TaskName "LifeBots" -ErrorAction SilentlyContinue | Out-Null
    Start-ScheduledTask -TaskName "LifeBots" -ErrorAction SilentlyContinue
}

foreach ($tool in "ssh", "scp", "tar") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "'$tool' not found - install the Windows OpenSSH client / tar" }
}

Step "Check the server is reachable ($Server)"
Remote "echo connected as `$(whoami) on `$(uname -sr)"

$stoppedLocal = $false
try {
    if (-not $KeepLocalRunning) {
        Step "Stop local bots (so the server is the only one polling Telegram)"
        StopLocal
        $stoppedLocal = $true
        Start-Sleep 3
    }

    Step "Pack the project (no .venv / logs / git)"
    $tgz = Join-Path $env:TEMP "lifebots-deploy.tgz"
    if (Test-Path $tgz) { Remove-Item $tgz -Force }
    & tar -czf $tgz --exclude=.venv --exclude=.git --exclude=logs --exclude=__pycache__ `
        --exclude=.pytest_cache --exclude=.ruff_cache --exclude=*.egg-info --exclude=*.tmp -C $root .
    if ($LASTEXITCODE -ne 0) { throw "tar failed" }
    "{0:N0} KB" -f ((Get-Item $tgz).Length / 1KB) | Write-Host

    if ($InstallDocker) {
        Step "Install Docker on the server (Ubuntu/Debian)"
        Remote "command -v docker >/dev/null 2>&1 || (curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker `$USER)"
    }
    Remote "docker compose version >/dev/null 2>&1 || sudo docker compose version >/dev/null 2>&1 || (echo 'Docker Compose missing - rerun with -InstallDocker' && exit 1)"

    Step "Make sure small servers have swap (1 GB RAM is tight while building images)"
    Remote "[ `$(free -m | awk '/^Swap:/{print `$2}') -gt 0 ] || (sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile && (grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null)); free -m | awk '/^(Mem|Swap):/'"

    Step "Upload and unpack into ~/$RemoteDir"
    Remote "mkdir -p $RemoteDir"
    & scp @scpArgs $tgz "${Server}:${RemoteDir}/lifebots.tgz"
    if ($LASTEXITCODE -ne 0) { throw "scp failed" }
    Remote "cd $RemoteDir && tar -xzf lifebots.tgz && rm lifebots.tgz && chmod 600 */.env"

    Step "Build and start the 4 bots"
    Remote "cd $RemoteDir && (docker compose up -d --build || sudo docker compose up -d --build)"
    Start-Sleep 15
    Remote "cd $RemoteDir && (docker compose ps || sudo docker compose ps)"

    Step "Can the server reach the Vietnamese data sources? (dry run: prints, sends nothing)"
    Remote "cd $RemoteDir && (docker compose run --rm -T price python -m price.bot --dry --now || sudo docker compose run --rm -T price python -m price.bot --dry --now) 2>&1 | head -30"
    Write-Host "If prices are missing above, the server's country/IP may be blocked by that site (see README)." -ForegroundColor Yellow

    Step "Recent logs"
    Remote "cd $RemoteDir && (docker compose logs --tail 4 || sudo docker compose logs --tail 4)"

    Write-Host "`nDONE. Bots now run on $Server. Local autostart is disabled." -ForegroundColor Green
    Write-Host "Check Telegram: send /status to a bot. Logs: ssh $Server 'cd $RemoteDir && docker compose logs -f'"
}
catch {
    Write-Host "`nFAILED: $($_.Exception.Message)" -ForegroundColor Red
    if ($stoppedLocal) {
        Write-Host "Rolling back: restarting the local bots." -ForegroundColor Yellow
        StartLocal
    }
    exit 1
}
