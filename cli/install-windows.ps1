# AdaDo CLI installer — Windows (PowerShell)
# Usage: irm https://adadoai.com/install-cli.ps1 | iex

$ErrorActionPreference = "Stop"
$ADO_URL = "https://adadoai.com"
$INSTALL_DIR = "$env:LOCALAPPDATA\ado"
$SCRIPT_URL = "$ADO_URL/cli/ado.py"

Write-Host ""
Write-Host "  Installing ado — AdaDo CLI" -ForegroundColor Magenta
Write-Host ""

# Check Python
$pyPath = (Get-Command python -ErrorAction SilentlyContinue)?.Source
if (-not $pyPath) {
    $pyPath = (Get-Command python3 -ErrorAction SilentlyContinue)?.Source
}
if (-not $pyPath) {
    Write-Host "  ✗ Python 3 is required." -ForegroundColor Red
    Write-Host "    Install from https://python.org or run:"
    Write-Host "    winget install Python.Python.3"
    exit 1
}

# Download ado.py
New-Item -ItemType Directory -Force -Path $INSTALL_DIR | Out-Null
$dest = "$INSTALL_DIR\ado.py"
Invoke-WebRequest -Uri $SCRIPT_URL -OutFile $dest

# Create ado.cmd wrapper
$wrapper = "@echo off`r`npython `"$dest`" %*"
Set-Content -Path "$INSTALL_DIR\ado.cmd" -Value $wrapper

# Add to PATH if not already there
$userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($userPath -notlike "*$INSTALL_DIR*") {
    [Environment]::SetEnvironmentVariable("PATH", "$userPath;$INSTALL_DIR", "User")
    Write-Host "  ✓ Added $INSTALL_DIR to PATH"
}

Write-Host "  ✓ ado installed to $INSTALL_DIR" -ForegroundColor Green
Write-Host ""
Write-Host "  Restart your terminal, then:"
Write-Host "    ado status           — check your Ada connection"
Write-Host "    ado                  — start chatting"
Write-Host "    ado config instance <url>  — point to your Ada instance"
Write-Host ""
