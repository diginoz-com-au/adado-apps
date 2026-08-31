# =============================================================================
#  AdaDo Installer (PowerShell)
#  Usage: powershell -NoProfile -ExecutionPolicy Bypass -Command "& { iex ((New-Object System.Net.WebClient).DownloadString('https://adadoai.com/install.ps1')) }"
# =============================================================================

param(
    [switch]$NoColor = $false
)

$ErrorActionPreference = "Stop"

# ── Colour functions ────────────────────────────────────────────────────────────
function Write-Info {
    param([string]$Message)
    if (-not $NoColor) {
        Write-Host "  → $Message" -ForegroundColor Cyan
    } else {
        Write-Host "  → $Message"
    }
}

function Write-Success {
    param([string]$Message)
    if (-not $NoColor) {
        Write-Host "  ✓ $Message" -ForegroundColor Green
    } else {
        Write-Host "  ✓ $Message"
    }
}

function Write-Warn {
    param([string]$Message)
    if (-not $NoColor) {
        Write-Host "  ⚠ $Message" -ForegroundColor Yellow
    } else {
        Write-Host "  ⚠ $Message"
    }
}

function Write-Error-Custom {
    param([string]$Message)
    if (-not $NoColor) {
        Write-Host "  ✗ $Message" -ForegroundColor Red
    } else {
        Write-Host "  ✗ $Message"
    }
}

function Write-Header {
    param([string]$Message)
    if (-not $NoColor) {
        Write-Host ""
        Write-Host $Message -ForegroundColor Cyan -BackgroundColor Black
    } else {
        Write-Host ""
        Write-Host $Message
    }
}

function Write-Divider {
    if (-not $NoColor) {
        Write-Host "──────────────────────────────────────────────" -ForegroundColor Cyan
    } else {
        Write-Host "──────────────────────────────────────────────"
    }
}

# ── Banner ──────────────────────────────────────────────────────────────────────
Write-Host ""
if (-not $NoColor) {
    Write-Host @"
    _       _       ___
   / \   __| | __ _|   \  ___
  / _ \ / _\`| / _\` | |) |/ _ \
 / ___ \ (_| | (_| |   /|  __/
/_/   \_\__,_|\__,_|_|\_\ \___|

"@ -ForegroundColor Cyan
}
Write-Host "  Your home. Your AI. Your rules."
Write-Host "  Self-hosted AI suite by Diginoz"
Write-Host ""
Write-Divider

# ── OS Detection ────────────────────────────────────────────────────────────────
Write-Header "Checking your system"

$OS = if ($PSVersionTable.Platform -eq "Unix") { "Linux" } elseif ($PSVersionTable.OS -like "*Windows*" -or [System.Environment]::OSVersion.Platform -eq "Win32NT") { "Windows" } else { "Unknown" }
$OSVersion = [System.Environment]::OSVersion.VersionString

Write-Success "Detected: $OS $OSVersion"

# ── Helper: command exists ──────────────────────────────────────────────────────
function Test-CommandExists {
    param([string]$Command)
    $null = Get-Command $Command -ErrorAction SilentlyContinue
    return $?
}

# ── Docker check ────────────────────────────────────────────────────────────────
Write-Header "Checking Docker"

if (-not (Test-CommandExists "docker")) {
    Write-Error-Custom "Docker is not installed."
    Write-Host ""
    Write-Host "  Install it from:"
    Write-Host ""
    Write-Host "    Windows:  https://docs.docker.com/desktop/windows/install/"
    Write-Host "    WSL2:     https://docs.docker.com/desktop/wsl/"
    Write-Host "    Choco:    choco install docker-desktop"
    Write-Host ""
    exit 1
}

try {
    $DockerVersion = docker --version 2>$null | Select-String -Pattern '[0-9]+\.[0-9]+\.[0-9]+' -AllMatches | ForEach-Object { $_.Matches[0].Value }
    Write-Success "Docker $DockerVersion found"
} catch {
    Write-Error-Custom "Docker version check failed"
    exit 1
}

# Verify Docker daemon is running
try {
    docker info *>$null
    Write-Success "Docker daemon is running"
} catch {
    Write-Error-Custom "Docker daemon is not running."
    Write-Host ""
    Write-Host "  Start Docker Desktop and try again."
    Write-Host ""
    exit 1
}

# ── Docker Compose check ────────────────────────────────────────────────────────
Write-Header "Checking Docker Compose"

$ComposeCmd = ""

if (Test-CommandExists "docker") {
    try {
        docker compose version *>$null
        $ComposeVersion = docker compose version --short 2>$null
        Write-Success "Docker Compose v2 found ($ComposeVersion)"
        $ComposeCmd = "docker compose"
    } catch {
        Write-Error-Custom "Docker Compose v2 not found."
        Write-Host ""
        Write-Host "  Install: https://docs.docker.com/compose/install/"
        Write-Host ""
        exit 1
    }
}

if ([string]::IsNullOrEmpty($ComposeCmd)) {
    Write-Error-Custom "Docker Compose v2 is not available."
    exit 1
}

# ── Git check ───────────────────────────────────────────────────────────────────
Write-Header "Checking Git"

if (-not (Test-CommandExists "git")) {
    Write-Error-Custom "Git is not installed."
    Write-Host ""
    Write-Host "  Install from: https://git-scm.com/download/win"
    Write-Host "  or: choco install git"
    Write-Host ""
    exit 1
}

try {
    $GitVersion = git --version 2>$null | Select-String -Pattern '[0-9]+\.[0-9]+\.[0-9]+' -AllMatches | ForEach-Object { $_.Matches[0].Value }
    Write-Success "Git $GitVersion found"
} catch {
    Write-Error-Custom "Git version check failed"
    exit 1
}

# ── Clone repository ────────────────────────────────────────────────────────────
Write-Header "Installing AdaDo"

$InstallDir = Join-Path $env:USERPROFILE "adado"
$RepoUrl = "https://github.com/diginoz-com-au/adado-apps"

if (Test-Path (Join-Path $InstallDir ".git")) {
    Write-Info "AdaDo directory already exists at $InstallDir"
    Write-Info "Pulling latest changes..."
    try {
        Push-Location $InstallDir
        git pull --ff-only 2>$null
        Pop-Location
        Write-Success "Repository updated"
    } catch {
        Write-Warn "Could not fast-forward. Your local changes may conflict."
        Write-Warn "To reset: git -C $InstallDir reset --hard origin/main"
    }
} else {
    if ((Test-Path $InstallDir) -and @(Get-ChildItem $InstallDir -ErrorAction SilentlyContinue).Count -gt 0) {
        Write-Error-Custom "Directory $InstallDir exists and is not empty. Remove it first."
        exit 1
    }

    Write-Info "Cloning $RepoUrl → $InstallDir"
    try {
        git clone $RepoUrl $InstallDir
        Write-Success "Repository cloned"
    } catch {
        Write-Error-Custom "Failed to clone repository. Check your internet connection."
        exit 1
    }
}

Push-Location $InstallDir

# ── Environment configuration ───────────────────────────────────────────────────
Write-Header "Configuring environment"

$EnvFile = Join-Path $InstallDir ".env"
$EnvExample = Join-Path $InstallDir ".env.example"

if (-not (Test-Path $EnvExample)) {
    Write-Warn ".env.example not found — skipping environment setup."
    Write-Warn "You may need to create $EnvFile manually."
} else {
    if (Test-Path $EnvFile) {
        Write-Warn "$EnvFile already exists — skipping prompts (delete it to reconfigure)."
    } else {
        Write-Info "Copying .env.example → .env"
        Copy-Item $EnvExample $EnvFile

        Write-Host ""
        Write-Divider
        Write-Host "  Quick setup — press Enter to keep defaults"
        Write-Divider

        # Prompt: domain name
        Write-Host ""
        $DefaultDomain = "localhost"
        $UserDomain = Read-Host "  Domain name (e.g. adado.example.com) [$DefaultDomain]"
        $Domain = if ([string]::IsNullOrEmpty($UserDomain)) { $DefaultDomain } else { $UserDomain }

        # Prompt: admin password
        Write-Host ""
        $DefaultPass = -join ((1..20) | ForEach-Object { [char][int](Get-Random -Minimum 48 -Maximum 122) })
        $Host.UI.RawUI.ForegroundColor = 'Gray'
        $UserPass = Read-Host "  Admin password [auto-generated]" -AsSecureString
        $Host.UI.RawUI.ForegroundColor = 'White'
        Write-Host ""

        $AdminPass = if ($null -eq $UserPass -or $UserPass.Length -eq 0) {
            $DefaultPass
        } else {
            [System.Runtime.InteropServices.Marshal]::PtrToStringAuto([System.Runtime.InteropServices.Marshal]::SecureStringToCoTaskMemUnicode($UserPass))
        }

        # Update .env file
        $EnvContent = Get-Content $EnvFile -Raw

        if ($EnvContent -match "^DOMAIN=") {
            $EnvContent = $EnvContent -replace "^DOMAIN=.*", "DOMAIN=$Domain"
        } else {
            $EnvContent += "`r`nDOMAIN=$Domain"
        }

        if ($EnvContent -match "^ADMIN_PASSWORD=") {
            $EnvContent = $EnvContent -replace "^ADMIN_PASSWORD=.*", "ADMIN_PASSWORD=$AdminPass"
        } else {
            $EnvContent += "`r`nADMIN_PASSWORD=$AdminPass"
        }

        Set-Content $EnvFile $EnvContent

        Write-Success "Environment configured"
        if ($null -eq $UserPass -or $UserPass.Length -eq 0) {
            Write-Host ""
            if (-not $NoColor) {
                Write-Host "  Auto-generated admin password: $AdminPass" -ForegroundColor Yellow
                Write-Host "  Save this — it won't be shown again." -ForegroundColor Yellow
            } else {
                Write-Host "  Auto-generated admin password: $AdminPass"
                Write-Host "  Save this — it won't be shown again."
            }
            Write-Host ""
        }
    }
}

# ── Launch core stack ───────────────────────────────────────────────────────────
Write-Header "Starting AdaDo (core profile)"

Write-Info "Running: $ComposeCmd --profile core up -d"
Write-Host ""

try {
    & powershell -NoProfile -Command "$ComposeCmd --profile core up -d"
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose exited with code $LASTEXITCODE"
    }
} catch {
    Write-Host ""
    Write-Error-Custom "Docker Compose failed. Common causes:"
    Write-Host "    • Port already in use — check:  Get-NetTCPConnection -LocalPort 80,443"
    Write-Host "    • Permission denied  — ensure Docker is running with admin"
    Write-Host "    • Missing .env vars  — edit:    $EnvFile"
    exit 1
}

# ── Read configured domain for final message ────────────────────────────────────
$PortalDomain = "localhost"
if (Test-Path $EnvFile) {
    $PortalDomain = @(Select-String -Path $EnvFile -Pattern "^DOMAIN=" | ForEach-Object { $_.Line -replace "^DOMAIN=", "" -replace '"', "" -replace "'", "" })
    if ($PortalDomain.Count -gt 0) {
        $PortalDomain = $PortalDomain[0]
    } else {
        $PortalDomain = "localhost"
    }
}

# ── Success ─────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Divider
if (-not $NoColor) {
    Write-Host "  AdaDo is running!" -ForegroundColor Green
} else {
    Write-Host "  AdaDo is running!"
}
Write-Divider
Write-Host ""
Write-Host "  Portal:      http://$PortalDomain"
Write-Host "  Install dir: $InstallDir"
Write-Host "  Config:      $EnvFile"
Write-Host ""
Write-Host "  Add more apps:"
Write-Host "    cd $InstallDir"
Write-Host "    $ComposeCmd --profile <appname> up -d"
Write-Host ""
Write-Host "  View running services:"
Write-Host "    $ComposeCmd ps"
Write-Host ""
Write-Host "  Stop everything:"
Write-Host "    $ComposeCmd down"
Write-Host ""
Write-Host "  Docs:        https://github.com/diginoz-com-au/adado-apps"
Write-Host ""
Write-Divider
Write-Host ""

Pop-Location
