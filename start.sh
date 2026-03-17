#!/bin/bash

set -euo pipefail

# ============================================================================
# ai-dev-platform startup script
# ============================================================================
# Checks prerequisites, sets up virtual environment, runs migrations, and
# starts all microservices (API, orchestrator, agents) as background processes.
#
# Usage:
#   chmod +x start.sh
#   ./start.sh
#
# To stop all services:
#   ./stop.sh
# ============================================================================

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ============================================================================
# Colour codes for terminal output
# ============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'  # No colour

# ============================================================================
# Helper functions
# ============================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
    echo -e "${GREEN}✅${NC} $*"
}

log_warning() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*"
}

die() {
    log_error "$@"
    exit 1
}

# ============================================================================
# Prerequisite checks
# ============================================================================

check_prerequisites() {
    log_info "Checking prerequisites..."

    # Python 3
    if ! command -v python3 &> /dev/null; then
        die "python3 not found. Please install Python 3.11 or later."
    fi
    local py_version
    py_version=$(python3 --version | awk '{print $2}')
    log_success "python3 $py_version found"

    # PostgreSQL
    if ! command -v psql &> /dev/null; then
        die "psql not found. Please install PostgreSQL 15 or later."
    fi
    local psql_version
    psql_version=$(psql --version | awk '{print $3}')
    log_success "psql $psql_version found"

    # Redis
    if ! command -v redis-cli &> /dev/null; then
        die "redis-cli not found. Please install Redis 7 or later."
    fi
    if ! redis-cli ping &> /dev/null; then
        die "Redis is not running. Start Redis: redis-server"
    fi
    log_success "redis-cli responding (ping OK)"

    # Docker
    if ! command -v docker &> /dev/null; then
        die "docker not found. Please install Docker Engine."
    fi
    if ! docker info &> /dev/null; then
        die "Docker daemon not running. Start Docker: sudo systemctl start docker"
    fi
    log_success "docker daemon running"

    # Git
    if ! command -v git &> /dev/null; then
        die "git not found. Please install Git."
    fi
    log_success "git found"
}

# ============================================================================
# Virtual environment
# ============================================================================

setup_venv() {
    log_info "Setting up virtual environment..."

    if [[ ! -d .venv ]]; then
        python3 -m venv .venv
        log_success "Virtual environment created (.venv)"
    else
        log_success "Virtual environment already exists (.venv)"
    fi

    # Activate venv
    # shellcheck disable=SC1091
    source .venv/bin/activate

    log_success "Virtual environment activated"
}

# ============================================================================
# Dependencies and migrations
# ============================================================================

install_dependencies() {
    log_info "Installing dependencies from requirements.txt..."
    pip install -r requirements.txt -q
    log_success "Dependencies installed"
}

run_migrations() {
    log_info "Running database migrations..."
    python -m alembic upgrade head
    log_success "Migrations completed"
}

build_sandbox_image() {
    log_info "Building sandbox Docker image (ai-sandbox)..."
    docker build -t ai-sandbox ./sandbox/ > /dev/null
    log_success "Sandbox image built (ai-sandbox)"
}

# ============================================================================
# Service startup
# ============================================================================

# Store PIDs of background processes for cleanup
declare -a PIDS=()

cleanup() {
    log_warning "Shutting down services..."
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
    done
    log_info "All services stopped"
    exit 0
}

start_services() {
    log_info "Starting all services as background processes..."

    # API server
    log_info "  → Starting API (port 8000)..."
    uvicorn api.main:app --host 0.0.0.0 --port 8000 > /tmp/api.log 2>&1 &
    PIDS+=($!)

    # Orchestrator worker
    log_info "  → Starting orchestrator worker..."
    python -m orchestrator.worker > /tmp/orchestrator.log 2>&1 &
    PIDS+=($!)

    # Agent workers
    log_info "  → Starting backend_agent worker..."
    python -m agents.backend_agent.worker > /tmp/backend_agent.log 2>&1 &
    PIDS+=($!)

    log_info "  → Starting frontend_agent worker..."
    python -m agents.frontend_agent.worker > /tmp/frontend_agent.log 2>&1 &
    PIDS+=($!)

    log_info "  → Starting database_agent worker..."
    python -m agents.database_agent.worker > /tmp/database_agent.log 2>&1 &
    PIDS+=($!)

    log_info "  → Starting qa_agent worker..."
    python -m agents.qa_agent.worker > /tmp/qa_agent.log 2>&1 &
    PIDS+=($!)

    # Brief pause to allow services to start
    sleep 2

    # Check if API is responding
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        log_success "API is responding"
    else
        log_warning "API not yet responding (give it 5-10 seconds to start...)"
    fi

    log_success "All services started. API at http://localhost:8000"
}

# ============================================================================
# Main execution
# ============================================================================

main() {
    log_info "ai-dev-platform startup"
    log_info "Script directory: $SCRIPT_DIR"

    check_prerequisites
    setup_venv
    install_dependencies
    run_migrations
    build_sandbox_image
    start_services

    # Trap signals for graceful shutdown
    trap cleanup SIGINT SIGTERM

    log_info "Services are running. Press Ctrl+C to stop."
    log_info ""
    log_info "Service logs available at:"
    log_info "  - API:                /tmp/api.log"
    log_info "  - Orchestrator:       /tmp/orchestrator.log"
    log_info "  - BackendAgent:       /tmp/backend_agent.log"
    log_info "  - FrontendAgent:      /tmp/frontend_agent.log"
    log_info "  - DatabaseAgent:      /tmp/database_agent.log"
    log_info "  - QAAgent:            /tmp/qa_agent.log"
    log_info ""

    # Keep the script alive
    wait
}

main "$@"
