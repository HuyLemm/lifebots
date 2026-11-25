<#
.SYNOPSIS
  Publish LifeBots to GitHub and run the 4 bots on GitHub Actions (free, no server, no card).

.DESCRIPTION
  1. creates a PUBLIC repo and pushes the code (free runner minutes are unlimited only for public repos)
  2. stores your 4 bot tokens + chat id as encrypted GitHub Secrets (never in the code)
  3. seeds the `state` branch from your local state so nothing is re-sent
  4. stops the local bots (two places polling one token fight each other)
  5. starts the first run

.EXAMPLE
  .\deploy\github_setup.ps1 -Repo lifebots -IKnowItIsPublic

.NOTES
  What becomes PUBLIC: the code and config.toml files (your keywords, channels, thresholds).
  What stays PRIVATE: bot tokens and chat id (Secrets); .env files are git-ignored and never pushed.
  Needs GitHub CLI: winget install GitHub.cli   then   gh auth login
#>
param(
    [string] $Repo = "lifebots",
    [switch] $IKnowItIsPublic,
    [switch] $KeepLocalRunning
)

# Windows PowerShell 5.1 turns ANY stderr line of a native program (gh/git print progress there)
# into a terminating error when this is "Stop". Native failures are checked via $LASTEXITCODE instead.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

if (-not $IKnowItIsPublic) {
    Write-Host @"
This will create a PUBLIC GitHub repository containing your code and config.toml files
(keywords, channels, thresholds). Bot tokens and chat id are NOT included: they go into
encrypted GitHub Secrets. Re-run with -IKnowItIsPublic to confirm.
"@ -ForegroundColor Yellow
    exit 1
}

Step "Check GitHub CLI"
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Host "GitHub CLI not found. Install it, then open a NEW terminal and rerun:" -ForegroundColor Yellow
    Write-Host "  winget install --id GitHub.cli -e"
    exit 1
}
& gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Not logged in. Run:  gh auth login   (choose GitHub.com, HTTPS, login with browser)" -ForegroundColor Yellow
    exit 1
}
& gh auth setup-git *> $null
$owner = (& gh api user --jq .login).Trim()
Write-Host "Logged in as $owner"

Step "Safety scan: nothing secret may be tracked by git"
$tracked = & git ls-files
$envFiles = $tracked | Where-Object { $_ -match '(^|/)\.env$|state\.json$|\.log$' }
if ($envFiles) { throw "Secret-looking files are tracked: $($envFiles -join ', ')" }
$leaks = $tracked | ForEach-Object { Select-String -Path $_ -Pattern '\d{8,10}:AA[A-Za-z0-9_-]{30,}' -ErrorAction SilentlyContinue }
if ($leaks) { throw "A Telegram token pattern was found in tracked files: $($leaks[0].Path)" }
Write-Host "clean"

function ReadEnv([string]$bot, [string]$key) {
    $line = Get-Content "$root\$bot\.env" -ErrorAction Stop | Where-Object { $_ -match "^$key=" } | Select-Object -First 1
    if (-not $line) { throw "$key missing in $bot\.env" }
    return ($line -split '=', 2)[1].Trim()
}

$stoppedLocal = $false
try {
    Step "Create the repo and push the code"
    & gh repo view "$owner/$Repo" *> $null
    if ($LASTEXITCODE -ne 0) {
        & gh repo create $Repo --public --source $root --remote origin --push
        if ($LASTEXITCODE -ne 0) { throw "gh repo create failed" }
    } else {
        Write-Host "repo $owner/$Repo already exists: pushing to it"
        if (-not (& git remote | Where-Object { $_ -eq "origin" })) { & git remote add origin "https://github.com/$owner/$Repo.git" }
        & git push -u origin main
        if ($LASTEXITCODE -ne 0) { throw "git push failed" }
    }

    Step "Store tokens as encrypted Secrets (values are never printed)"
    $secrets = @{
        PRICE_BOT_TOKEN   = ReadEnv "price" "BOT_TOKEN"
        NEWS_BOT_TOKEN    = ReadEnv "news" "BOT_TOKEN"
        WEATHER_BOT_TOKEN = ReadEnv "weather" "BOT_TOKEN"
        STUFF_BOT_TOKEN   = ReadEnv "stuff" "BOT_TOKEN"
        TELEGRAM_CHAT_ID  = ReadEnv "price" "TELEGRAM_CHAT_ID"
    }
    foreach ($name in $secrets.Keys) {
        $secrets[$name] | & gh secret set $name --repo "$owner/$Repo"
        if ($LASTEXITCODE -ne 0) { throw "could not set secret $name" }
        Write-Host "  set $name"
    }

    if (-not $KeepLocalRunning) {
        Step "Stop local bots (only GitHub should poll Telegram now)"
        Stop-ScheduledTask -TaskName "LifeBots" -ErrorAction SilentlyContinue
        Disable-ScheduledTask -TaskName "LifeBots" -ErrorAction SilentlyContinue | Out-Null
        Get-CimInstance Win32_Process |
            Where-Object { $_.CommandLine -match [regex]::Escape($root) -and $_.Name -match "python" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        $stoppedLocal = $true
        Start-Sleep 3
    }

    Step "Seed the `state` branch from your local state (so nothing is re-sent)"
    $tmp = Join-Path $env:TEMP "lifebots-state"
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    New-Item -ItemType Directory -Force $tmp | Out-Null
    foreach ($f in "price\state.json", "news\state.json", "weather\state.json", "stuff\state.json", "price\history.csv") {
        if (Test-Path "$root\$f") {
            New-Item -ItemType Directory -Force (Join-Path $tmp (Split-Path $f)) | Out-Null
            Copy-Item "$root\$f" (Join-Path $tmp $f)
        }
    }
    Push-Location $tmp
    try {
        & git init -q
        if ($LASTEXITCODE -ne 0) { throw "git init failed" }
        & git checkout -q -b state
        & git add -A
        & git -c user.name=lifebots -c user.email=lifebots@users.noreply.github.com commit -q -m "seed state"
        if ($LASTEXITCODE -ne 0) { throw "could not commit the seed state" }
        & git push -q -f "https://github.com/$owner/$Repo.git" state
        if ($LASTEXITCODE -ne 0) { throw "could not push the state branch" }
    } finally { Pop-Location }

    Step "Start the first run"
    & gh workflow enable lifebots.yml --repo "$owner/$Repo" *> $null
    & gh workflow run lifebots.yml --repo "$owner/$Repo"
    Write-Host "`nDONE. Watch it: https://github.com/$owner/$Repo/actions" -ForegroundColor Green
    Write-Host "From now on it runs every ~5 minutes by itself. To change code: edit, git commit, git push."
    Write-Host "Back to local: Enable-ScheduledTask LifeBots; Start-ScheduledTask LifeBots"
}
catch {
    Write-Host "`nFAILED: $($_.Exception.Message)" -ForegroundColor Red
    if ($stoppedLocal) {
        Write-Host "Rolling back: restarting the local bots." -ForegroundColor Yellow
        Enable-ScheduledTask -TaskName "LifeBots" -ErrorAction SilentlyContinue | Out-Null
        Start-ScheduledTask -TaskName "LifeBots" -ErrorAction SilentlyContinue
    }
    exit 1
}
