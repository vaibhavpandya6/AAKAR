#!/bin/bash
#
# view_logs.sh - View all service logs in the terminal with color-coded labels
#
# Usage:
#   ./view_logs.sh
#

set -euo pipefail

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m'  # No color

# Log files
LOGS=(
    "/tmp/api.log"
    "/tmp/orchestrator.log"
    "/tmp/bootstrap_agent.log"
    "/tmp/backend_agent.log"
    "/tmp/frontend_agent.log"
    "/tmp/database_agent.log"
    "/tmp/qa_agent.log"
)

# Labels and colors for each service
declare -A LABELS=(
    ["/tmp/api.log"]="API"
    ["/tmp/orchestrator.log"]="ORCH"
    ["/tmp/bootstrap_agent.log"]="BOOT"
    ["/tmp/backend_agent.log"]="BACK"
    ["/tmp/frontend_agent.log"]="FRNT"
    ["/tmp/database_agent.log"]="DATB"
    ["/tmp/qa_agent.log"]="QA  "
)

declare -A COLORS=(
    ["/tmp/api.log"]="$BLUE"
    ["/tmp/orchestrator.log"]="$GREEN"
    ["/tmp/bootstrap_agent.log"]="$CYAN"
    ["/tmp/backend_agent.log"]="$MAGENTA"
    ["/tmp/frontend_agent.log"]="$YELLOW"
    ["/tmp/database_agent.log"]="$RED"
    ["/tmp/qa_agent.log"]="$GREEN"
)

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Aakar Platform - Live Logs Viewer${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "Watching logs from:"
for log in "${LOGS[@]}"; do
    if [[ -f "$log" ]]; then
        echo -e "  ✓ ${log}"
    else
        echo -e "  ✗ ${log} (not found - will be created when service starts)"
    fi
done
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Function to tail a log file with colored prefix
tail_with_label() {
    local logfile=$1
    local label="${LABELS[$logfile]}"
    local color="${COLORS[$logfile]}"

    # Create log file if it doesn't exist
    touch "$logfile" 2>/dev/null || true

    tail -f "$logfile" 2>/dev/null | while IFS= read -r line; do
        echo -e "${color}[${label}]${NC} ${line}"
    done
}

# Start tailing all logs in parallel
for log in "${LOGS[@]}"; do
    tail_with_label "$log" &
done

# Wait for all background processes
wait
