#Requires -Version 5.1
<#
.SYNOPSIS
    Stops all ai-dev-platform services started by start.ps1.

.DESCRIPTION
    Reads process IDs from the .pids file written by start.ps1 and terminates
    each process. Optionally also stops the Docker Compose infrastructure
    (Redis + PostgreSQL) with the -StopInfra flag.

.PARAMETER StopInfra
    Also stop the Redis + PostgreSQL Docker Compose services.
    By default these are left running so the next start.ps1 -SkipInfra
    launch is instant.

.EXAMPLE
    .\stop.ps1
    .\stop.ps1 -StopInfra
#>

param(
    [switch]$StopInfra
)

$scriptRoot = $PSScriptRoot
if (-not $scriptRoot) {
    $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}
if (-not $scriptRoot) {
    $scriptRoot = (Get-Location).Path
}
Set-Location $scriptRoot

function Write-Info([string]$msg)    { Write-Host "  [INFO] $msg" -ForegroundColor Cyan }
function Write-Success([string]$msg) { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn([string]$msg)    { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }

Write-Host ""
Write-Host "  Stopping ai-dev-platform services..." -ForegroundColor Magenta
Write-Host ""

# ============================================================================
# Stop Python / uvicorn processes via saved PIDs
# ============================================================================

$pidsFile = Join-Path $scriptRoot ".pids"

if (Test-Path $pidsFile) {
    $savedPids = Get-Content $pidsFile | Where-Object { $_ -match "^\d+$" }
    if ($savedPids.Count -eq 0) {
        Write-Warn ".pids file is empty — no processes to stop."
    } else {
        foreach ($pid in $savedPids) {
            $intPid = [int]$pid
            $proc   = Get-Process -Id $intPid -ErrorAction SilentlyContinue
            if ($proc) {
                try {
                    Stop-Process -Id $intPid -Force
                    Write-Success "Stopped PID $intPid ($($proc.ProcessName))"
                } catch {
                    Write-Warn "Could not stop PID $intPid — $($_.Exception.Message)"
                }
            } else {
                Write-Warn "PID $intPid not found (already exited?)"
            }
        }
        Remove-Item $pidsFile -Force -ErrorAction SilentlyContinue
        Write-Info ".pids file removed"
    }
} else {
    Write-Warn ".pids file not found — attempting pattern-based cleanup..."

    # Fallback: kill by command-line pattern (less precise, kills ALL matching)
    $patterns = @(
        "uvicorn api.main:app",
        "orchestrator.worker",
        "agents.backend_agent.worker",
        "agents.frontend_agent.worker",
        "agents.database_agent.worker",
        "agents.qa_agent.worker"
    )

    foreach ($pattern in $patterns) {
        Get-Process | Where-Object { $_.CommandLine -like "*$pattern*" } |
            ForEach-Object {
                Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
                Write-Success "Stopped $($_.Id) matched '$pattern'"
            }
    }
}

# ============================================================================
# Optionally stop Docker Compose infrastructure
# ============================================================================

if ($StopInfra) {
    Write-Host ""
    Write-Info "Stopping Docker Compose services (Redis + PostgreSQL)..."
    $composeFile = Join-Path $scriptRoot "docker-compose.dev.yml"
    if (Test-Path $composeFile) {
        docker compose -f $composeFile down
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Docker Compose services stopped"
        } else {
            Write-Warn "docker compose down returned non-zero exit code"
        }
    } else {
        Write-Warn "docker-compose.dev.yml not found — skipping"
    }
} else {
    Write-Info "Redis + PostgreSQL containers left running."
    Write-Info "Use  .\stop.ps1 -StopInfra  to also stop them."
    Write-Info "Or:  docker compose -f docker-compose.dev.yml down"
}

Write-Host ""
Write-Host "  [OK] All services stopped." -ForegroundColor Green
Write-Host ""
