#Requires -Version 5.1
<#
.SYNOPSIS
    View all service logs in the terminal with color-coded labels

.DESCRIPTION
    Monitors all log files in the logs/ directory and displays them in real-time
    with color-coded service labels. Press Ctrl+C to stop.

.EXAMPLE
    .\view_logs.ps1
#>

$scriptRoot = $PSScriptRoot
if (-not $scriptRoot) {
    $scriptRoot = (Get-Location).Path
}

$logsDir = Join-Path $scriptRoot "logs"

# Service labels and colors
$services = @(
    @{ File = "api.log"; Label = "API "; Color = "Cyan" }
    @{ File = "orchestrator.log"; Label = "ORCH"; Color = "Green" }
    @{ File = "bootstrap_agent.log"; Label = "BOOT"; Color = "DarkCyan" }
    @{ File = "backend_agent.log"; Label = "BACK"; Color = "Magenta" }
    @{ File = "frontend_agent.log"; Label = "FRNT"; Color = "Yellow" }
    @{ File = "database_agent.log"; Label = "DATB"; Color = "Red" }
    @{ File = "qa_agent.log"; Label = "QA  "; Color = "DarkGreen" }
)

Write-Host ""
Write-Host "===============================================" -ForegroundColor Blue
Write-Host "  Aakar Platform - Live Logs Viewer" -ForegroundColor Blue
Write-Host "===============================================" -ForegroundColor Blue
Write-Host ""
Write-Host "Watching logs from:" -ForegroundColor White
foreach ($svc in $services) {
    $logPath = Join-Path $logsDir $svc.File
    if (Test-Path $logPath) {
        Write-Host "  [OK] $($svc.File)" -ForegroundColor Green
    } else {
        Write-Host "  [--] $($svc.File) (not found - will be created when service starts)" -ForegroundColor Yellow
    }
}
Write-Host ""
Write-Host "Press Ctrl+C to stop" -ForegroundColor Gray
Write-Host ""

# Store file positions for each log
$filePositions = @{}
foreach ($svc in $services) {
    $logPath = Join-Path $logsDir $svc.File
    $filePositions[$logPath] = 0
}

# Function to read new lines from a file
function Read-NewLines {
    param(
        [string]$FilePath,
        [int]$FromPosition,
        [string]$Label,
        [string]$Color
    )

    if (-not (Test-Path $FilePath)) {
        return $FromPosition
    }

    try {
        $content = Get-Content -Path $FilePath -Raw -ErrorAction SilentlyContinue
        if (-not $content) {
            return $FromPosition
        }

        if ($content.Length -le $FromPosition) {
            return $FromPosition
        }

        $newContent = $content.Substring($FromPosition)
        $lines = $newContent -split "`r`n|`n"

        foreach ($line in $lines) {
            if ($line.Trim()) {
                Write-Host "[$Label] " -ForegroundColor $Color -NoNewline
                Write-Host $line
            }
        }

        return $content.Length
    }
    catch {
        return $FromPosition
    }
}

# Main loop - poll log files continuously
try {
    while ($true) {
        foreach ($svc in $services) {
            $logPath = Join-Path $logsDir $svc.File
            $newPos = Read-NewLines -FilePath $logPath -FromPosition $filePositions[$logPath] -Label $svc.Label -Color $svc.Color
            $filePositions[$logPath] = $newPos
        }

        Start-Sleep -Milliseconds 200
    }
}
catch {
    Write-Host ""
    Write-Host "Stopped log viewer" -ForegroundColor Yellow
}
