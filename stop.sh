#!/bin/bash

set -euo pipefail

# ============================================================================
# ai-dev-platform shutdown script
# ============================================================================
# Stops all microservices (API, orchestrator, agents) started by start.sh.
#
# Usage:
#   chmod +x stop.sh
#   ./stop.sh
# ============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
    echo -e "${GREEN}✅${NC} $*"
}

log_warning() {
    echo -e "${RED}[WARN]${NC} $*"
}

# ============================================================================
# Kill services
# ============================================================================

main() {
    log_info "Stopping ai-dev-platform services..."

    # Kill uvicorn (API)
    if pgrep -f "uvicorn api.main:app" > /dev/null; then
        log_info "Stopping API (uvicorn)..."
        pkill -f "uvicorn api.main:app" || true
        log_success "API stopped"
    fi

    # Kill orchestrator worker
    if pgrep -f "python -m orchestrator.worker" > /dev/null; then
        log_info "Stopping orchestrator worker..."
        pkill -f "python -m orchestrator.worker" || true
        log_success "Orchestrator worker stopped"
    fi

    # Kill backend_agent worker
    if pgrep -f "python -m agents.backend_agent.worker" > /dev/null; then
        log_info "Stopping backend_agent worker..."
        pkill -f "python -m agents.backend_agent.worker" || true
        log_success "Backend agent worker stopped"
    fi

    # Kill frontend_agent worker
    if pgrep -f "python -m agents.frontend_agent.worker" > /dev/null; then
        log_info "Stopping frontend_agent worker..."
        pkill -f "python -m agents.frontend_agent.worker" || true
        log_success "Frontend agent worker stopped"
    fi

    # Kill database_agent worker
    if pgrep -f "python -m agents.database_agent.worker" > /dev/null; then
        log_info "Stopping database_agent worker..."
        pkill -f "python -m agents.database_agent.worker" || true
        log_success "Database agent worker stopped"
    fi

    # Kill qa_agent worker
    if pgrep -f "python -m agents.qa_agent.worker" > /dev/null; then
        log_info "Stopping qa_agent worker..."
        pkill -f "python -m agents.qa_agent.worker" || true
        log_success "QA agent worker stopped"
    fi

    log_success "All services stopped"
}

main "$@"
