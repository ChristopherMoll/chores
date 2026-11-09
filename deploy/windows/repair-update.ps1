#Requires -RunAsAdministrator
# Repair the older installer already downloaded to TEMP, then retry that update.
# This script is standalone: it can be saved to Downloads on the affected PC.
# Keep this file ASCII for Windows PowerShell 5.1.
param(
    [string]$Installer = (Join-Path $env:TEMP "chores-main\deploy\windows\install.ps1")
)
$ErrorActionPreference = "Stop"

function Repair-InstallerSource([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Installer not found at $Path. Pass -Installer with the path to the unpacked install.ps1."
    }
    $source = [IO.File]::ReadAllText($Path)
    $marker = "# Chores ACL repair: effective file grants before directory inheritance."
    if ($source.Contains("function Set-DataPermissions") -or $source.Contains($marker)) { return }
    $pattern = '(?m)^[ \t]*icacls(?:\.exe)? \$Data /inheritance:r /grant:r [^\r\n]* /T /Q \| Out-Null[ \t]*\r?$'
    $permissionCommands = [regex]::Matches($source, $pattern)
    if ($permissionCommands.Count -ne 1) {
        throw "The old permission command was not found exactly once. Use the corrected installer instead."
    }
    $match = $permissionCommands[0]
    $old = $match.Value.TrimEnd("`r")
    if (-not $old.Contains("(OI)(CI)")) { return } # Already repaired by this script.
    $direct = $old.Replace("(OI)(CI)", "")
    $inherit = $old.Replace("/inheritance:r ", "").Replace("/grant:r ", "/grant ")
    $replacement = @(
        $marker,
        $direct,
        'if ($LASTEXITCODE -ne 0) {',
        '    & takeown.exe /F $Data /A /R /D Y | Out-Null',
        '    Assert-Ok "Recovering ownership of the chores data files"',
        ("    " + $direct.TrimStart()),
        '    Assert-Ok "Repairing file permissions"',
        '}',
        $inherit
    ) -join "`r`n"
    $fixed = $source.Remove($match.Index, $match.Length).Insert($match.Index, $replacement)
    $tokens = $null
    $errors = $null
    [void][Management.Automation.Language.Parser]::ParseInput($fixed, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw "The repaired installer did not parse. The original was left unchanged." }
    if (-not (Test-Path -LiteralPath "$Path.before-acl-fix")) {
        Copy-Item -LiteralPath $Path -Destination "$Path.before-acl-fix"
    }
    [IO.File]::WriteAllText($Path, $fixed, [Text.Encoding]::ASCII)
    Write-Host "Corrected the downloaded installer's file permissions command."
}

Repair-InstallerSource $Installer
Write-Host "Retrying the update with the corrected installer."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Installer
exit $LASTEXITCODE
