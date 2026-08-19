#!/bin/bash
set -e

echo "================================================"
echo "BNK-Forge Backend Startup"
echo "================================================"

# Fix volume permissions on first run
# Docker volumes are created as root, but we run as bnkforge (uid 1000)
# The Makefile install target handles permissions, but we also check here
# in case the container is started directly
DIRS_TO_CHECK="/app/state /app/keys /app/agent-token /app/projects /app/workspaces /app/helm_charts /app/bfb-cache"
for dir in $DIRS_TO_CHECK; do
    if [ -d "$dir" ] && [ ! -w "$dir" ]; then
        echo "Warning: $dir is not writable by bnkforge user"
        echo "   Run: docker exec -u root <container> chown -R bnkforge:bnkforge $dir"
    fi
done

# Check for .env file (informational only)
if [ ! -f "/.env" ] && [ ! -f "/app/.env" ]; then
    echo "Note: No .env file found - using docker-compose environment variables"
fi

# S14-004: Skip DB migrations for celery workers (they don't need to run migrations)
# Only the backend (uvicorn) container should handle migrations
if echo "$@" | grep -q "celery"; then
    echo "Celery process detected - skipping database migrations"
    echo "Starting application..."
    exec "$@"
fi

# Database initialization/migration
echo ""
cd /app

# S14-004: Use Python to check DB instead of psql (which isn't installed in the image)
# Uses DATABASE_URL env var which is always available (not POSTGRES_HOST/USER/etc.)
if ! python -c "
from sqlalchemy import create_engine, text
import os
engine = create_engine(os.environ['DATABASE_URL'])
with engine.connect() as conn:
    result = conn.execute(text('SELECT 1 FROM alembic_version LIMIT 1'))
    result.fetchone()
" 2>/dev/null; then
    echo "Fresh database detected - initializing schema..."
    python init_db.py
    if [ $? -ne 0 ]; then
        echo "Database initialization failed"
        exit 1
    fi
else
    echo "Existing database detected - running migrations..."
    alembic upgrade head
    if [ $? -ne 0 ]; then
        echo "Migration failed"
        exit 1
    fi
fi
echo "Database ready"

# Execute CMD from Dockerfile
echo ""
echo "Starting application..."
exec "$@"
