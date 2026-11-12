# Keeps chores up to date from GitHub. Task Scheduler runs this every 15 minutes as SYSTEM.
# It installs the newest commit on main once that exact commit has passed the tests.
# If the install fails, it puts back the previous code and database, and skips that
# commit until a newer one arrives.
# Keep this file ASCII: Windows PowerShell 5.1 misreads UTF-8 without a BOM.
param(
    [string]$App = (Join-Path $env:ProgramFiles "chores"),
    [string]$Data = (Join-Path $env:ProgramData "chores"),
    # Not under $Data: SYSTEM runs code from here, and the app's own account can write to $Data.
    [string]$State = (Join-Path $env:ProgramFiles "chores-update")
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Repo = "ChristopherMoll/chores"
$Api = "https://api.github.com/repos/$Repo"
$Agent = "chores-updater"
$TaskPath = "\chores\"
$Port = 8087

$Log = Join-Path $State "update.log"
$Status = Join-Path $State "status.txt"
$Deployed = Join-Path $State "deployed.txt"
$Failed = Join-Path $State "failed.txt"
$Work = Join-Path $State "work"
$Previous = Join-Path $State "previous"
$DbCopies = Join-Path $State "db"
$Db = Join-Path $Data "chores.sqlite3"
$EnvFile = Join-Path $Data "chores.env"

function Write-Log($message) {
    Add-Content -Path $Log -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $message"
}

function Read-Value($path) {
    if (Test-Path $path) { return ([string](Get-Content $path -TotalCount 1)).Trim() }
    return ""
}

# For conditions that repeat every 15 minutes: log them only when they change.
function Write-Status($message) {
    if ((Read-Value $Status) -ne $message) {
        Write-Log $message
        Set-Content -Path $Status -Value $message
    }
    Publish-Status $message
}

# Display-only copy in the data folder. Privileged updater decisions always
# read their protected files under $State, never this app-writable report.
function Publish-Status($message) {
    try {
        if (-not (Test-Path -LiteralPath $Data)) { return }
        $report = @{ checked_at = [DateTime]::UtcNow.ToString("o"); status = [string]$message; deployed = (Read-Value $Deployed) }
        $report | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Data "update-status.json") -Encoding UTF8
    } catch {
        Write-Log "Could not publish the display-only updater status."
    }
}

function Get-AppTasks {
    # Never this script's own task: stopping it would end the update halfway through.
    Get-ScheduledTask -TaskPath $TaskPath -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -ne "update" }
}

function Stop-App {
    foreach ($task in Get-AppTasks) {
        $task | Disable-ScheduledTask | Out-Null
        $task | Stop-ScheduledTask
    }
    # An old server still listening would pass the new version's health check.
    foreach ($attempt in 1..15) {
        if (-not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) { return }
        Start-Sleep -Seconds 1
    }
    throw "The old server is still listening on port $Port."
}

function Start-App {
    foreach ($task in Get-AppTasks) { $task | Enable-ScheduledTask | Out-Null }
    Start-ScheduledTask -TaskPath $TaskPath -TaskName "serve"
}

function Invoke-Install($folder) {
    $script = Join-Path $folder "deploy\windows\install.ps1"
    $out = Join-Path $State "install-out.txt"
    $err = Join-Path $State "install-err.txt"
    $arguments = @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", "`"$script`"")
    $process = Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -Wait -PassThru `
        -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err
    foreach ($file in $out, $err) {
        if ((Get-Item $file).Length -gt 0) { Add-Content -Path $Log -Value (Get-Content $file) }
    }
    return ($process.ExitCode -eq 0)
}

if (-not (Test-Path $State)) {
    New-Item -ItemType Directory -Path $State | Out-Null
    # The database copy holds password hashes, so only SYSTEM and Administrators get in.
    icacls $State /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Setting permissions on $State failed." }
}
if ((Test-Path $Log) -and (Get-Item $Log).Length -gt 1MB) { Move-Item -Path $Log -Destination "$Log.old" -Force }

# Nothing here touches the running app, so any problem just means trying again next time.
try {
    if (-not (Test-Path $Db)) { throw "No database at $Db. Run install.ps1 by hand first." }
    Publish-Status "Checking for updates."

    $sha = (Invoke-RestMethod "$Api/commits/main" -UserAgent $Agent -TimeoutSec 30).sha
    if ($sha -notmatch '^[0-9a-f]{40}$') { throw "GitHub returned an unexpected commit id." }
    $short = $sha.Substring(0, 7)
    if ($sha -eq (Read-Value $Deployed)) { Write-Status "Up to date at $short."; exit 0 }
    if ($sha -eq (Read-Value $Failed)) { Write-Status "Skipping $short after a previous failed update."; exit 0 }

    $runs = @((Invoke-RestMethod "$Api/actions/workflows/tests.yml/runs?head_sha=$sha&branch=main&event=push&per_page=1" `
        -UserAgent $Agent -TimeoutSec 30).workflow_runs)
    if ($runs.Count -eq 0 -or $runs[0].status -ne "completed") {
        Write-Status "Waiting for the tests to finish on $short."
        exit 0
    }
    if ($runs[0].conclusion -ne "success") {
        Write-Status "Not installing ${short}: its tests ended with '$($runs[0].conclusion)'."
        exit 0
    }

    if (Test-Path $Work) { Remove-Item -Path $Work -Recurse -Force }
    New-Item -ItemType Directory -Path $Work | Out-Null
    $zip = Join-Path $Work "source.zip"
    Invoke-WebRequest "https://github.com/$Repo/archive/$sha.zip" -UseBasicParsing -UserAgent $Agent -OutFile $zip -TimeoutSec 300
    Expand-Archive -Path $zip -DestinationPath $Work
    $New = Join-Path $Work "chores-$sha"
    if (-not (Test-Path (Join-Path $New "deploy\windows\install.ps1"))) { throw "The download has no installer in it." }
} catch {
    Write-Status "Update check failed, will try again: $($_.Exception.Message)"
    exit 0
}

Write-Log "Updating to $short."
Write-Status "Updating to $short."

# Save what's needed to go back. The app is stopped first so the copy of the database is final.
try {
    Stop-App
    robocopy $App $Previous /MIR /R:2 /W:2 /NFL /NDL /NJH /NJS /NP /XD .venv __pycache__ | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Saving the current code failed (robocopy exit code $LASTEXITCODE)." }
    $global:LASTEXITCODE = 0
    $Python = Join-Path $App ".venv\Scripts\python.exe"
    $DbCopy = & $Python (Join-Path $App "manage.py") --env-file $EnvFile backup $DbCopies --keep 1
    if ($LASTEXITCODE -ne 0) { throw "Saving the database failed (exit code $LASTEXITCODE)." }
    $DbCopy = ([string]$DbCopy).Trim()
    if (-not (Test-Path $DbCopy)) { throw "The saved database is missing: $DbCopy" }
} catch {
    Write-Log "Update to $short abandoned, nothing was changed: $($_.Exception.Message)"
    Set-Content -Path $Failed -Value $sha
    Write-Status "Update to $short abandoned; running the previous version."
    Remove-Item -Path $Work -Recurse -Force -ErrorAction SilentlyContinue
    try { Start-App } catch { Write-Log "COULD NOT RESTART THE APP: $($_.Exception.Message)" }
    exit 1
}

$installed = $false
try { $installed = Invoke-Install $New } catch { Write-Log "The installer could not run: $($_.Exception.Message)" }

if ($installed) {
    Set-Content -Path $Deployed -Value $sha
    Remove-Item -Path $Failed -Force -ErrorAction SilentlyContinue
    Remove-Item -Path $Work -Recurse -Force
    Write-Log "Updated to $short."
    Write-Status "Updated to $short."
    exit 0
}

# The new version may have changed the database, and the old code can't be trusted to read it.
# Nobody could have used the new version: it failed before the install finished.
Write-Log "Update to $short failed. Restoring the previous version and database."
Set-Content -Path $Failed -Value $sha
$restored = $false
try {
    Stop-App
    Copy-Item -Path $DbCopy -Destination $Db -Force
    Remove-Item -Path "$Db-wal", "$Db-shm" -Force -ErrorAction SilentlyContinue
    $restored = Invoke-Install $Previous
} catch {
    Write-Log "Restoring failed: $($_.Exception.Message)"
}
Remove-Item -Path $Work -Recurse -Force -ErrorAction SilentlyContinue

if ($restored) {
    Write-Log "Restored the previous version. Skipping $short until a newer commit arrives."
    Write-Status "Update to $short failed; running the previous version."
    exit 1
}
Write-Log "RESTORE FAILED: the app is probably down. The saved database is $DbCopy and the saved code is $Previous."
Write-Status "Update to $short failed and the restore failed too."
exit 2
