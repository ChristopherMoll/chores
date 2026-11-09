#Requires -RunAsAdministrator
# Install or update chores on Windows 11 (including Windows on a Raspberry Pi).
# Needs Python 3.11+ installed for all users, so it's under C:\Program Files.
# From an Administrator PowerShell, in the unpacked project folder:
#   powershell -ExecutionPolicy Bypass -File .\deploy\windows\install.ps1
# Keep this file ASCII: Windows PowerShell 5.1 misreads UTF-8 without a BOM.
$ErrorActionPreference = "Stop"

$Src = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$App = Join-Path $env:ProgramFiles "chores"
$Data = Join-Path $env:ProgramData "chores"
$Logs = Join-Path $Data "logs"
$Backups = Join-Path $Data "backups"
$EnvFile = Join-Path $Data "chores.env"
$TaskPath = "\chores\"

function Assert-Ok($what) {
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)." }
}

# Find Python. It has to be an all-users install, because the app runs as LOCAL SERVICE
# and can't read anything under a person's AppData folder.
$BasePython = Get-ChildItem (Join-Path $env:ProgramFiles "Python3*\python.exe") -ErrorAction SilentlyContinue |
    Sort-Object { [int]([regex]::Match($_.Directory.Name, '^Python3(\d+)').Groups[1].Value) } -Descending |
    Select-Object -First 1
if (-not $BasePython) {
    throw "No Python found under $env:ProgramFiles. Install Python 3.13 for all users, then run this again."
}
$BasePython = $BasePython.FullName
& $BasePython -c "import sys; sys.exit(sys.version_info < (3, 11))"
Assert-Ok "The Python 3.11+ check for $BasePython"
Write-Host "Using $BasePython"

# Stop anything already running so the code and virtualenv can be replaced.
$existing = Get-ScheduledTask -TaskPath $TaskPath -ErrorAction SilentlyContinue
foreach ($task in $existing) {
    $task | Disable-ScheduledTask | Out-Null
    $task | Stop-ScheduledTask
}

# Copy the code. The excluded folders are also left alone in the destination.
robocopy $Src $App /MIR /R:2 /W:2 /NFL /NDL /NJH /NJS /NP /XD .git .venv instance backups __pycache__ .pytest_cache
if ($LASTEXITCODE -ge 8) { throw "Copying the code failed (robocopy exit code $LASTEXITCODE)." }
$global:LASTEXITCODE = 0

$Python = Join-Path $App ".venv\Scripts\python.exe"
& $BasePython -m venv (Join-Path $App ".venv")
Assert-Ok "Creating the virtualenv"
& $Python -m pip install -q --upgrade pip
Assert-Ok "Upgrading pip"
# --prefer-binary: on ARM64 the newest cryptography can lag behind on prebuilt wheels,
# and building it from source needs a Rust and C compiler toolchain.
& $Python -m pip install -q --prefer-binary -r (Join-Path $App "requirements.txt")
Assert-Ok "Installing dependencies"
# LOCAL SERVICE can't write bytecode here, so compile it now to keep the every-minute run fast.
& $Python -m compileall -q (Join-Path $App "chores")
Assert-Ok "Compiling the app"

# Data folder: only SYSTEM, Administrators and the app's own account can get in.
New-Item -ItemType Directory -Force -Path $Data, $Logs, $Backups | Out-Null
icacls $Data /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-19:(OI)(CI)M" /T /Q | Out-Null
Assert-Ok "Setting folder permissions"

if (-not (Test-Path $EnvFile)) {
    $lines = (Get-Content (Join-Path $App "deploy\chores.env.example")) -replace '^CHORES_INSTANCE=.*', "CHORES_INSTANCE=$Data"
    [IO.File]::WriteAllLines($EnvFile, [string[]]$lines)
}

function Invoke-Manage {
    & $Python (Join-Path $App "manage.py") --env-file $EnvFile @args
    Assert-Ok "manage.py $args"
}

if (Test-Path (Join-Path $Data "chores.sqlite3")) {
    Write-Host "Existing database: backing it up, then applying any schema changes."
    Invoke-Manage backup $Backups
    Invoke-Manage migrate
} else {
    Write-Host ""
    Write-Host "First install: creating the database and the three accounts."
    Invoke-Manage init
}

# Scheduled tasks replace the systemd units used on Linux.
$account = ([Security.Principal.SecurityIdentifier]"S-1-5-19").Translate([Security.Principal.NTAccount]).Value
$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType ServiceAccount -RunLevel Limited

function Register-ChoresTask($Name, $Command, $Triggers, $TimeLimit) {
    $arguments = "`"$(Join-Path $App 'manage.py')`" --env-file `"$EnvFile`" --log-file `"$(Join-Path $Logs "$Name.log")`" $Command"
    $action = New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory $App
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -MultipleInstances IgnoreNew -ExecutionTimeLimit $TimeLimit -Priority 5
    Register-ScheduledTask -TaskPath $TaskPath -TaskName $Name -Action $action -Trigger $Triggers `
        -Settings $settings -Principal $principal -Force | Out-Null
}

# The repeating trigger is the crash restart: if serve is still running, IgnoreNew makes it a no-op.
Register-ChoresTask "serve" "serve" @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5))
) (New-TimeSpan -Seconds 0)
Register-ChoresTask "notify" "notify" @(
    (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 1))
) (New-TimeSpan -Minutes 5)
Register-ChoresTask "backup" "backup `"$Backups`" --keep 30" @(
    (New-ScheduledTaskTrigger -Daily -At "3:30AM")
) (New-TimeSpan -Hours 1)

Start-ScheduledTask -TaskPath $TaskPath -TaskName "serve"

$up = $false
foreach ($attempt in 1..20) {
    Start-Sleep -Seconds 1
    try {
        Invoke-WebRequest "http://127.0.0.1:8087/login" -UseBasicParsing -TimeoutSec 3 | Out-Null
        $up = $true
        break
    } catch { }
}

Write-Host ""
if ($up) {
    Write-Host "chores is running on 127.0.0.1:8087."
    Write-Host "Next: add this block to your Caddyfile and reload Caddy:"
    Write-Host ""
    Get-Content (Join-Path $App "deploy\Caddyfile") | Where-Object { $_ -notmatch '^#' } | Write-Host
} else {
    Write-Host "chores did not answer on 127.0.0.1:8087. Check $(Join-Path $Logs 'serve.log')"
    exit 1
}
