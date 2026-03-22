#!/bin/bash

# ============================================================================
# Debug capture script - collects diagnostic information
# ============================================================================
# Run this script when experiencing issues with LLM or logs
# Output: debug_report.txt
# ============================================================================

set -euo pipefail

OUTPUT_FILE="debug_report.txt"

echo "==================================================================" > "$OUTPUT_FILE"
echo "AI Dev Platform - Debug Report" >> "$OUTPUT_FILE"
echo "Generated: $(date)" >> "$OUTPUT_FILE"
echo "==================================================================" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

# Get auth token
echo "Obtaining auth token..." | tee -a "$OUTPUT_FILE"
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"admin123"}' \
  | jq -r '.access_token' 2>/dev/null || echo "FAILED")

if [ "$TOKEN" = "FAILED" ] || [ -z "$TOKEN" ] || [ "$TOKEN" = "null" ]; then
    echo "ERROR: Could not obtain auth token" | tee -a "$OUTPUT_FILE"
    echo "Is the API server running on port 8000?" | tee -a "$OUTPUT_FILE"
    exit 1
fi

echo "✅ Auth token obtained" | tee -a "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

# ============================================================================
# Test 1: Health Check
# ============================================================================

echo "==================================================================" >> "$OUTPUT_FILE"
echo "TEST 1: Health Check" >> "$OUTPUT_FILE"
echo "==================================================================" >> "$OUTPUT_FILE"

curl -s -X GET http://localhost:8000/health | jq >> "$OUTPUT_FILE" 2>&1
echo "" >> "$OUTPUT_FILE"

# ============================================================================
# Test 2: LLM Connectivity
# ============================================================================

echo "==================================================================" >> "$OUTPUT_FILE"
echo "TEST 2: LLM Connectivity" >> "$OUTPUT_FILE"
echo "==================================================================" >> "$OUTPUT_FILE"

curl -s -X GET http://localhost:8000/diagnostics/llm \
  -H "Authorization: Bearer $TOKEN" | jq >> "$OUTPUT_FILE" 2>&1
echo "" >> "$OUTPUT_FILE"

# ============================================================================
# Test 3: Redis Streams
# ============================================================================

echo "==================================================================" >> "$OUTPUT_FILE"
echo "TEST 3: Redis Streams" >> "$OUTPUT_FILE"
echo "==================================================================" >> "$OUTPUT_FILE"

curl -s -X GET http://localhost:8000/diagnostics/redis \
  -H "Authorization: Bearer $TOKEN" | jq >> "$OUTPUT_FILE" 2>&1
echo "" >> "$OUTPUT_FILE"

# ============================================================================
# Test 4: Database
# ============================================================================

echo "==================================================================" >> "$OUTPUT_FILE"
echo "TEST 4: Database" >> "$OUTPUT_FILE"
echo "==================================================================" >> "$OUTPUT_FILE"

curl -s -X GET http://localhost:8000/diagnostics/database \
  -H "Authorization: Bearer $TOKEN" | jq >> "$OUTPUT_FILE" 2>&1
echo "" >> "$OUTPUT_FILE"

# ============================================================================
# Test 5: Log Query
# ============================================================================

echo "==================================================================" >> "$OUTPUT_FILE"
echo "TEST 5: Log Query Test" >> "$OUTPUT_FILE"
echo "==================================================================" >> "$OUTPUT_FILE"

curl -s -X GET http://localhost:8000/diagnostics/logs/test \
  -H "Authorization: Bearer $TOKEN" | jq >> "$OUTPUT_FILE" 2>&1
echo "" >> "$OUTPUT_FILE"

# ============================================================================
# Test 6: Environment Variables
# ============================================================================

echo "==================================================================" >> "$OUTPUT_FILE"
echo "TEST 6: Environment Variables (Redacted)" >> "$OUTPUT_FILE"
echo "==================================================================" >> "$OUTPUT_FILE"

echo "GROQ_API_KEY: ${GROQ_API_KEY:+SET (hidden)} ${GROQ_API_KEY:-NOT SET}" >> "$OUTPUT_FILE"
echo "DATABASE_URL: ${DATABASE_URL:+SET (hidden)} ${DATABASE_URL:-NOT SET}" >> "$OUTPUT_FILE"
echo "REDIS_URL: ${REDIS_URL:-redis://localhost:6379}" >> "$OUTPUT_FILE"
echo "JWT_SECRET: ${JWT_SECRET:+SET (hidden)} ${JWT_SECRET:-NOT SET}" >> "$OUTPUT_FILE"
echo "ENVIRONMENT: ${ENVIRONMENT:-development}" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

# ============================================================================
# Test 7: Process Status
# ============================================================================

echo "==================================================================" >> "$OUTPUT_FILE"
echo "TEST 7: Running Processes" >> "$OUTPUT_FILE"
echo "==================================================================" >> "$OUTPUT_FILE"

echo "API Server:" >> "$OUTPUT_FILE"
ps aux | grep "[u]vicorn" >> "$OUTPUT_FILE" 2>&1 || echo "Not running" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

echo "Agent Workers:" >> "$OUTPUT_FILE"
ps aux | grep "[a]gents.*worker" >> "$OUTPUT_FILE" 2>&1 || echo "Not running" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

echo "Redis:" >> "$OUTPUT_FILE"
ps aux | grep "[r]edis-server" >> "$OUTPUT_FILE" 2>&1 || echo "Not running" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

echo "PostgreSQL:" >> "$OUTPUT_FILE"
ps aux | grep "[p]ostgres" >> "$OUTPUT_FILE" 2>&1 || echo "Not running" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

# ============================================================================
# Test 8: Recent API Logs
# ============================================================================

echo "==================================================================" >> "$OUTPUT_FILE"
echo "TEST 8: Recent API Logs (Last 50 Lines)" >> "$OUTPUT_FILE"
echo "==================================================================" >> "$OUTPUT_FILE"

if [ -f "/tmp/api.log" ]; then
    tail -50 /tmp/api.log >> "$OUTPUT_FILE"
else
    echo "API log file not found at /tmp/api.log" >> "$OUTPUT_FILE"
fi
echo "" >> "$OUTPUT_FILE"

# ============================================================================
# Test 9: Redis Stream Inspection
# ============================================================================

echo "==================================================================" >> "$OUTPUT_FILE"
echo "TEST 9: Redis Stream Information" >> "$OUTPUT_FILE"
echo "==================================================================" >> "$OUTPUT_FILE"

echo "Orchestrator Stream:" >> "$OUTPUT_FILE"
redis-cli XINFO STREAM stream:orchestrator 2>&1 >> "$OUTPUT_FILE" || echo "Stream not found or Redis not available" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

echo "Backend Agent Stream:" >> "$OUTPUT_FILE"
redis-cli XINFO STREAM stream:backend_agent 2>&1 >> "$OUTPUT_FILE" || echo "Stream not found" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

# ============================================================================
# Summary
# ============================================================================

echo "==================================================================" >> "$OUTPUT_FILE"
echo "DEBUG REPORT COMPLETE" >> "$OUTPUT_FILE"
echo "==================================================================" >> "$OUTPUT_FILE"
echo "Report saved to: $OUTPUT_FILE" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

echo ""
echo "✅ Debug report generated: $OUTPUT_FILE"
echo ""
echo "Review the report to identify issues:"
echo "  - TEST 2 (LLM) should show 'status: ok'"
echo "  - TEST 4 (Database) should show project/log counts"
echo "  - TEST 5 (Logs) should show 'status: ok'"
echo ""
echo "If you see errors, check:"
echo "  1. Environment variables in .env"
echo "  2. Service status (Redis, PostgreSQL)"
echo "  3. Recent API logs in TEST 8"
echo ""
