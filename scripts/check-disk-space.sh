#!/usr/bin/env bash
# === Disk Space Monitor ===
#
# Checks disk usage and warns if approaching capacity.
# Exit codes:
#   0 = OK (< 70%)
#   1 = WARNING (70-85%)
#   2 = CRITICAL (> 85%)
#
# Run before docker builds to catch disk issues early.

set -euo pipefail

THRESHOLD_WARNING=70
THRESHOLD_CRITICAL=85

# Get disk usage percentage (strip the % sign)
USAGE=$(df / | grep -v Filesystem | awk '{print $5}' | sed 's/%//')

echo "Disk usage: ${USAGE}%"

if [ "$USAGE" -ge "$THRESHOLD_CRITICAL" ]; then
  echo ""
  echo "========================================="
  echo "  ⚠️  CRITICAL: Disk usage above 85%"
  echo "========================================="
  echo ""
  echo "Available space:"
  df -h / | grep -v Filesystem
  echo ""
  echo "Docker disk usage:"
  docker system df 2>/dev/null || echo "(docker not running or no access)"
  echo ""
  echo "To reclaim space:"
  echo "  ./scripts/docker-cleanup.sh"
  echo ""
  exit 2
elif [ "$USAGE" -ge "$THRESHOLD_WARNING" ]; then
  echo ""
  echo "========================================="
  echo "  ⚠️  WARNING: Disk usage above 70%"
  echo "========================================="
  echo ""
  echo "Available space:"
  df -h / | grep -v Filesystem
  echo ""
  echo "Consider running: ./scripts/docker-cleanup.sh"
  echo ""
  exit 1
else
  echo "✓ Disk space OK (< 70%)"
  exit 0
fi
