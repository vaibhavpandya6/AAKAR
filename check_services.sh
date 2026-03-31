#!/bin/bash
# Quick service check script

echo "🔍 Checking services..."
echo ""

# Check Redis
echo "Redis (6379):"
if nc -z localhost 6379 2>/dev/null || (echo "X" | telnet localhost 6379 2>&1 | grep "Connected" > /dev/null); then
    echo "  ✓ Running"
else
    echo "  ✗ Not running"
    echo "  Start with: docker run -d -p 6379:6379 redis:latest"
fi

echo ""

# Check PostgreSQL
echo "PostgreSQL (5432):"
if nc -z localhost 5432 2>/dev/null || (echo "X" | telnet localhost 5432 2>&1 | grep "Connected" > /dev/null); then
    echo "  ✓ Running"
else
    echo "  ✗ Not running"
    echo "  Start with: docker run -d -p 5432:5432 -e POSTGRES_USER=user -e POSTGRES_PASSWORD=password -e POSTGRES_DB=aidevplatform postgres:15"
fi

echo ""

# Check API Server
echo "API Server (8000):"
if nc -z localhost 8000 2>/dev/null || curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo "  ✓ Running"
else
    echo "  ✗ Not running"
    echo "  Start with: python start_server.py"
fi

echo ""
echo "Done!"
