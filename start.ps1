#Requires -Version 5.1
<#
.SYNOPSIS
    Starts the ai-dev-platform on Windows.

.DESCRIPTION
    Checks prerequisites, spins up Redis + PostgreSQL via Docker Compose,
    creates a Python virtual environment, installs dependencies, runs database
    migrations, builds the sandbox image, and launches all six platform services
    as background processes.

    Process IDs are saved to .pids so stop.ps1 can terminate them cleanly.

.PARAMETER SkipInfra
    Skip starting Docker Compose (Redis + PostgreSQL). Use when they are
    already running from a previous session.

.EXAMPLE
    .\start.ps1
    .\start.ps1 -SkipInfra
#>

param(
    [switch]$SkipInfra
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ============================================================================
# Resolve script root — works whether called directly or via .bat launcher
# ============================================================================

$scriptRoot = $PSScriptRoot
if (-not $scriptRoot) {
    $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}
if (-not $scriptRoot) {
    $scriptRoot = (Get-Location).Path
}
Set-Location $scriptRoot

# Log directory
$logsDir = Join-Path $scriptRoot "logs"
if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir | Out-Null
}

# ============================================================================
# Colours / helpers
# ============================================================================

function Write-Info([string]$msg) {
    Write-Host "  [INFO] $msg" -ForegroundColor Cyan
}
function Write-Success([string]$msg) {
    Write-Host "  ✓ $msg" -ForegroundColor Green
}
function Write-Warn([string]$msg) {
    Write-Host "  [WARN] $msg" -ForegroundColor Yellow
}
function Write-Fail([string]$msg) {
    Write-Host "`n  [ERROR] $msg`n" -ForegroundColor Red
}
function Exit-WithError([string]$msg) {
    Write-Fail $msg
    exit 1
}

Write-Host ""
Write-Host "  ╔══════════════════════════════════════╗" -ForegroundColor Magenta
Write-Host "  ║    ai-dev-platform  |  Windows       ║" -ForegroundColor Magenta
Write-Host "  ╚══════════════════════════════════════╝" -ForegroundColor Magenta
Write-Host ""

# ============================================================================
# Step 1 – Prerequisite checks
# ============================================================================

Write-Info "Checking prerequisites..."

# Python (try py launcher first, then python)
$pythonCmd = $null
foreach ($candidate in @("py", "python", "python3")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $pythonCmd = $candidate
        break
    }
}
if (-not $pythonCmd) {
    Exit-WithError @"
Python not found.
  Install from: https://www.python.org/downloads/
  Make sure to check 'Add Python to PATH' during installation.
"@
}
$pyVer = & $pythonCmd --version 2>&1
Write-Success "Python: $pyVer"

# Docker CLI
if (-not (Get-Command "docker" -ErrorAction SilentlyContinue)) {
    Exit-WithError @"
Docker not found.
  Install Docker Desktop: https://www.docker.com/products/docker-desktop
  After install, start Docker Desktop and wait for it to finish loading.
"@
}

# Docker daemon
$ErrorActionPreference = "Continue"
docker info 2>&1 | Out-Null
$ErrorActionPreference = "Stop"
if ($LASTEXITCODE -ne 0) {
    Exit-WithError @"
Docker daemon is not running.
  1. Open Docker Desktop from the Start Menu.
  2. Wait until the Docker whale icon in the taskbar stops animating.
  3. Re-run this script.
"@
}
Write-Success "Docker Desktop is running"

# Git
if (-not (Get-Command "git" -ErrorAction SilentlyContinue)) {
    Exit-WithError @"
Git not found.
  Install from: https://git-scm.com/download/win
"@
}
Write-Success "Git is available"

# .env file
if (-not (Test-Path (Join-Path $scriptRoot ".env"))) {
    Exit-WithError @"
.env file not found.
  Run:  copy .env.example .env
  Then edit .env and fill in OPENAI_API_KEY, APP_SECRET_KEY, etc.
  For Windows, set POSTGRES_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/aidevplatform
"@
}
Write-Success ".env file found"

# ============================================================================
# Step 2 – Start Redis + PostgreSQL via Docker Compose
# ============================================================================

Write-Host ""
if ($SkipInfra) {
    Write-Info "Skipping Docker Compose (-SkipInfra flag set)"
} else {
    Write-Info "Starting Redis + PostgreSQL via Docker Compose..."
    $ErrorActionPreference = "Continue"
    docker compose -f docker-compose.dev.yml up -d 2>&1 | ForEach-Object {
        if ($_ -match "(Started|Running|Created|healthy)") {
            Write-Success $_.ToString().Trim()
        }
    }
    $ErrorActionPreference = "Stop"
    if ($LASTEXITCODE -ne 0) {
        Exit-WithError "docker compose failed. Run: docker compose -f docker-compose.dev.yml logs"
    }

    # ── Wait for PostgreSQL to pass health check ─────────────────────────────
    Write-Info "Waiting for PostgreSQL to become healthy..."
    $maxWait = 60   # seconds
    $elapsed = 0
    $pgReady = $false

    while ($elapsed -lt $maxWait) {
        $ErrorActionPreference = "Continue"
        $health = docker inspect --format "{{.State.Health.Status}}" aidev_postgres 2>&1
        $ErrorActionPreference = "Stop"
        if ($health -eq "healthy") {
            $pgReady = $true
            break
        }
        Start-Sleep -Seconds 2
        $elapsed += 2
        Write-Host "    ... waiting ($elapsed`s) - status: $health" -ForegroundColor DarkGray
    }
    if (-not $pgReady) {
        Exit-WithError "PostgreSQL did not become healthy after ${maxWait}s. Check: docker compose -f docker-compose.dev.yml logs postgres"
    }
    Write-Success "PostgreSQL is healthy"

    # ── Wait for Redis ────────────────────────────────────────────────────────
    Write-Info "Waiting for Redis to become healthy..."
    $elapsed = 0
    $redisReady = $false
    while ($elapsed -lt 30) {
        $ErrorActionPreference = "Continue"
        $health = docker inspect --format "{{.State.Health.Status}}" aidev_redis 2>&1
        $ErrorActionPreference = "Stop"
        if ($health -eq "healthy") {
            $redisReady = $true
            break
        }
        Start-Sleep -Seconds 2
        $elapsed += 2
    }
    if (-not $redisReady) {
        Exit-WithError "Redis did not become healthy. Check: docker compose -f docker-compose.dev.yml logs redis"
    }
    Write-Success "Redis is healthy"
}

# ============================================================================
# Step 3 – Python virtual environment
# ============================================================================

Write-Host ""
Write-Info "Setting up virtual environment..."

$venvPath   = Join-Path $scriptRoot ".venv"
$pythonExe  = Join-Path $venvPath "Scripts\python.exe"
$uvicornExe = Join-Path $venvPath "Scripts\uvicorn.exe"
$pipExe     = Join-Path $venvPath "Scripts\pip.exe"
$alembicExe = Join-Path $venvPath "Scripts\alembic.exe"
$activatePs1 = Join-Path $venvPath "Scripts\Activate.ps1"

if (-not (Test-Path $venvPath)) {
    Write-Info "Creating .venv ..."
    & $pythonCmd -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Exit-WithError "python -m venv failed. Ensure python3-venv is installed."
    }
    Write-Success "Virtual environment created"
} else {
    Write-Success "Virtual environment already exists"
}

# Activate for the current shell (so pip / alembic resolve correctly)
$ErrorActionPreference = "Continue"
& $activatePs1
$ErrorActionPreference = "Stop"

# ============================================================================
# Step 4 – Install dependencies
# ============================================================================

Write-Host ""
Write-Info "Installing Python dependencies (this may take a minute on first run)..."
& $pipExe install -r (Join-Path $scriptRoot "requirements.txt") -q
if ($LASTEXITCODE -ne 0) {
    Exit-WithError "pip install failed. Check requirements.txt."
}
Write-Success "Dependencies installed"

# ============================================================================
# Step 5 – Database migrations
# ============================================================================

Write-Host ""
Write-Info "Running Alembic migrations..."
& $alembicExe upgrade head
if ($LASTEXITCODE -ne 0) {
    Exit-WithError @"
Alembic migration failed.
  Ensure POSTGRES_URL in .env is set to:
  POSTGRES_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/aidevplatform
"@
}
Write-Success "Database schema up to date"

# ============================================================================
# Step 6 – Build sandbox Docker image
# ============================================================================

Write-Host ""
Write-Info "Building sandbox Docker image (ai-sandbox)..."
$sandboxDir = Join-Path $scriptRoot "sandbox"
docker build -t ai-sandbox $sandboxDir 2>&1 | Tee-Object -FilePath (Join-Path $logsDir "sandbox-build.log") | Out-Null
if ($LASTEXITCODE -ne 0) {
    Exit-WithError "docker build failed. See logs\sandbox-build.log for details."
}
Write-Success "Sandbox image built (ai-sandbox)"

# ============================================================================
# Step 7 – Start all platform services
# ============================================================================

Write-Host ""
Write-Info "Starting platform services as background processes..."

# PYTHONPATH must include the project root so absolute imports resolve
$env:PYTHONPATH = $scriptRoot

$processIds = @()

function Start-Service([string]$label, [string[]]$args, [string]$logName) {
    $logFile = Join-Path $logsDir "$logName.log"
    $errFile = Join-Path $logsDir "$logName.err"
    $p = Start-Process `
        -FilePath $pythonExe `
        -ArgumentList $args `
        -WorkingDirectory $scriptRoot `
        -RedirectStandardOutput $logFile `
        -RedirectStandardError  $errFile `
        -NoNewWindow `
        -PassThru
    Write-Success "Started $label  (PID $($p.Id))  → logs\$logName.log"
    return $p.Id
}

# API (uvicorn via python -m uvicorn for reliable module resolution)
$processIds += Start-Service "API server       " `
    @("-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000") `
    "api"

# Orchestrator
$processIds += Start-Service "Orchestrator     " `
    @("-m", "orchestrator.worker") `
    "orchestrator"

# Agent workers
$processIds += Start-Service "BackendAgent     " `
    @("-m", "agents.backend_agent.worker") `
    "backend_agent"

$processIds += Start-Service "FrontendAgent    " `
    @("-m", "agents.frontend_agent.worker") `
    "frontend_agent"

$processIds += Start-Service "DatabaseAgent    " `
    @("-m", "agents.database_agent.worker") `
    "database_agent"

$processIds += Start-Service "QAAgent          " `
    @("-m", "agents.qa_agent.worker") `
    "qa_agent"

# ── Save PIDs so stop.ps1 can find them ─────────────────────────────────────
$pidsFile = Join-Path $scriptRoot ".pids"
$processIds -join "`n" | Set-Content -Path $pidsFile -Encoding UTF8
Write-Info "PIDs saved to .pids"

# ============================================================================
# Step 8 – Health check
# ============================================================================

Write-Host ""
Write-Info "Waiting for API to be ready..."
$apiReady = $false
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 2
    $ErrorActionPreference = "Continue"
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing -TimeoutSec 3
        if ($resp.StatusCode -eq 200) { $apiReady = $true; break }
    } catch { }
    $ErrorActionPreference = "Stop"
    Write-Host "    ... waiting ($([int]($i*2+2))s)" -ForegroundColor DarkGray
}

Write-Host ""
if ($apiReady) {
    Write-Host "  ✅ All services started. API at http://localhost:8000" -ForegroundColor Green
} else {
    Write-Warn "API did not respond within 30s. It may still be starting."
    Write-Warn "Check: Get-Content logs\api.log -Tail 30"
}

Write-Host ""
Write-Host "  ┌─────────────────────────────────────────────────────────┐" -ForegroundColor DarkCyan
Write-Host "  │  Service logs:                                           │" -ForegroundColor DarkCyan
Write-Host "  │    API:           logs\api.log                          │" -ForegroundColor DarkCyan
Write-Host "  │    Orchestrator:  logs\orchestrator.log                 │" -ForegroundColor DarkCyan
Write-Host "  │    BackendAgent:  logs\backend_agent.log                │" -ForegroundColor DarkCyan
Write-Host "  │    FrontendAgent: logs\frontend_agent.log               │" -ForegroundColor DarkCyan
Write-Host "  │    DatabaseAgent: logs\database_agent.log               │" -ForegroundColor DarkCyan
Write-Host "  │    QAAgent:       logs\qa_agent.log                     │" -ForegroundColor DarkCyan
Write-Host "  │                                                          │" -ForegroundColor DarkCyan
Write-Host "  │  To stop:  .\stop.ps1                                   │" -ForegroundColor DarkCyan
Write-Host "  └─────────────────────────────────────────────────────────┘" -ForegroundColor DarkCyan
Write-Host ""
