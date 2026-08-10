#!/usr/bin/env bash
# === Setup Weekly Docker Cleanup Cron Job ===
#
# Installs a weekly cron job (Sunday 2 AM) to automatically clean Docker disk usage.
# Logs output to /var/log/docker-cleanup.log for audit.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLEANUP_SCRIPT="$SCRIPT_DIR/docker-cleanup.sh"
CRON_LINE="0 2 * * 0 $CLEANUP_SCRIPT >> /var/log/docker-cleanup.log 2>&1"

echo ""
echo "========================================="
echo "  Setup Docker Cleanup Cron Job"
echo "========================================="
echo ""

# Verify cleanup script exists
if [ ! -f "$CLEANUP_SCRIPT" ]; then
  echo "ERROR: Cleanup script not found at $CLEANUP_SCRIPT"
  exit 1
fi

# Make cleanup script executable
chmod +x "$CLEANUP_SCRIPT"
echo "✓ Cleanup script is executable: $CLEANUP_SCRIPT"

# Check if cron job already exists
if crontab -l 2>/dev/null | grep -q "docker-cleanup.sh"; then
  echo ""
  echo "WARNING: A docker-cleanup cron job already exists:"
  crontab -l 2>/dev/null | grep "docker-cleanup.sh"
  echo ""
  read -p "Replace it? [y/N] " -n 1 -r
  echo ""
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted. No changes made."
    exit 0
  fi
  # Remove existing cleanup cron
  (crontab -l 2>/dev/null | grep -v "docker-cleanup.sh") | crontab -
fi

# Add new cron job
(crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -

echo ""
echo "========================================="
echo "✓ Cron job installed successfully!"
echo "========================================="
echo ""
echo "Schedule: Every Sunday at 2:00 AM"
echo "Log file: /var/log/docker-cleanup.log"
echo ""
echo "Current crontab:"
crontab -l
echo ""
echo "To test the cleanup manually: $CLEANUP_SCRIPT"
echo "To remove the cron job: crontab -e (then delete the docker-cleanup line)"
echo ""
