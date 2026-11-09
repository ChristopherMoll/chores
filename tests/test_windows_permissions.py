"""Exercise the installer ACL repair on real Windows files, not the live install."""
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows NTFS ACLs and PowerShell")
def test_installer_repairs_existing_files_and_preserves_future_inheritance(tmp_path):
    windows = Path(__file__).resolve().parents[1] / "deploy/windows"
    env = dict(os.environ, CHORES_ACL_TEST_DIR=str(tmp_path / "data"),
               CHORES_ACL_INSTALLER=str(windows / "install.ps1"), CHORES_ACL_REPAIR=str(windows / "repair-update.ps1"))
    script = r'''
$ErrorActionPreference = "Stop"
$Data = $env:CHORES_ACL_TEST_DIR
$realIcacls = (Get-Command icacls.exe -CommandType Application).Source
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$logs = Join-Path $Data "logs"
$envFile = Join-Path $Data "chores.env"
$oldLog = Join-Path $logs "old.log"
$newLog = Join-Path $logs "new.log"
New-Item -ItemType Directory -Path $Data, $logs | Out-Null
[IO.File]::WriteAllText($envFile, "dummy configuration")
[IO.File]::WriteAllText($oldLog, "dummy log")

try {
    $tokens = $null
    $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($env:CHORES_ACL_INSTALLER, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw ($errors | Out-String) }
    $functions = $ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -in @("Assert-Ok", "Set-DataPermissions")
    }, $false)
    if ($functions.Count -ne 2) { throw "Installer permission helpers were not found." }
    . ([scriptblock]::Create(($functions | ForEach-Object { $_.Extent.Text }) -join "`n"))

    # Reproduce the original update: effective file access disappears despite exit 0.
    & $realIcacls $Data /inheritance:r /grant:r "*${sid}:(OI)(CI)F" /T /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not create the regression fixture." }
    $blocked = $false
    try { [void][IO.File]::ReadAllText($envFile) } catch { $blocked = $true }
    if (-not $blocked) { throw "The fixture did not reproduce the unreadable settings file." }

    # Exercise the unchanged production helper with the current test user in the
    # service account's place. No Administrator token or live app data is needed.
    function icacls.exe {
        $translated = @($args | ForEach-Object { ([string]$_).Replace("*S-1-5-19:", "*${sid}:") })
        & $realIcacls @translated
    }
    Set-DataPermissions
    if ([IO.File]::ReadAllText($envFile) -ne "dummy configuration") { throw "Settings changed." }
    if ([IO.File]::ReadAllText($oldLog) -ne "dummy log") { throw "Log changed." }
    $fileAcl = Get-Acl -LiteralPath $envFile
    $rules = @($fileAcl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    if ($rules.Count -ne 3) { throw "The repaired file should allow only the three intended accounts." }
    $service = @($rules | Where-Object { $_.IdentityReference.Value -eq $sid })
    if ($service.Count -ne 1 -or ($service[0].FileSystemRights -band [Security.AccessControl.FileSystemRights]::Modify) -ne [Security.AccessControl.FileSystemRights]::Modify) {
        throw "The service account does not have effective Modify access."
    }
    if ($service[0].InheritanceFlags -ne [Security.AccessControl.InheritanceFlags]::None) {
        throw "A file was given directory inheritance flags."
    }
    [IO.File]::WriteAllText($newLog, "new log")
    $newRules = (Get-Acl -LiteralPath $newLog).GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])
    if (@($newRules | Where-Object { $_.IdentityReference.Value -eq $sid -and $_.IsInherited }).Count -ne 1) {
        throw "New nested files do not inherit service access."
    }
    Set-DataPermissions
    if ([IO.File]::ReadAllText($newLog) -ne "new log") { throw "A repeated update broke the new log." }

    # The standalone recovery tool patches the old downloaded installer in place.
    $repairAst = [Management.Automation.Language.Parser]::ParseFile($env:CHORES_ACL_REPAIR, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw ($errors | Out-String) }
    $repair = $repairAst.Find({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq "Repair-InstallerSource"
    }, $false)
    . ([scriptblock]::Create($repair.Extent.Text))
    $legacyPath = Join-Path (Split-Path $Data) "legacy-install.ps1"
    $badCommand = 'icacls $Data /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-19:(OI)(CI)M" /T /Q | Out-Null'
    [IO.File]::WriteAllText($legacyPath, $badCommand)
    Repair-InstallerSource $legacyPath
    $patched = [IO.File]::ReadAllText($legacyPath)
    if ($patched.Contains('/grant:r "*S-1-5-18:(OI)(CI)F"')) { throw "The invalid file grant remains." }
    & $realIcacls $Data /inheritance:r /grant:r "*${sid}:(OI)(CI)F" /T /Q | Out-Null
    . ([scriptblock]::Create($patched))
    if ([IO.File]::ReadAllText($envFile) -ne "dummy configuration") { throw "The recovered installer cannot read settings." }
    Repair-InstallerSource $legacyPath
    if ([IO.File]::ReadAllText($legacyPath) -ne $patched) { throw "A repeat repair changed the installer again." }
    Write-Output "Existing files repaired, inheritance preserved, repeated update passed."
} finally {
    # Return the disposable fixture to the test runner's normal inherited ACL.
    & $realIcacls $Data /reset /T /Q | Out-Null
}
'''
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "repeated update passed" in result.stdout
