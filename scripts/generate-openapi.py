#!/usr/bin/env python3
"""
Generate OpenAPI spec from the FastAPI application.

Usage:
    # Generate (overwrite backend/openapi.json):
    python scripts/generate-openapi.py

    # Check mode (fail if committed spec is stale):
    python scripts/generate-openapi.py --check

    # Or via make:
    make openapi          # generate
    make openapi-check    # check freshness

Output: backend/openapi.json (committed to repo, regenerated on schema changes)

The generated spec is the source of truth for:
  1. TypeScript type generation (openapi-typescript)
  2. CI staleness checks (fail if spec doesn't match running app)
  3. API documentation (/docs, /redoc)
"""
import json
import os
import sys

CHECK_MODE = "--check" in sys.argv

# Ensure backend/ is on the Python path
backend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, backend_dir)

# Set minimal environment variables so the app can initialize without
# a running database or Redis. These are only needed for module-level
# imports and config validation — we never actually start the server.
os.environ.setdefault("DATABASE_URL", "sqlite:///dummy.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379")

# Suppress noisy startup logs during spec generation
os.environ.setdefault("LOG_LEVEL", "ERROR")

# Import the app — this triggers all route/schema registration
from main import app  # noqa: E402

# Generate the OpenAPI spec
spec = app.openapi()

# Normalize: strip the version from info (it changes on every version bump
# and would cause false staleness failures in CI)
spec_for_compare = json.loads(json.dumps(spec))
spec_for_compare["info"]["version"] = "0.0.0"

output_path = os.path.join(backend_dir, "openapi.json")
paths = len(spec.get("paths", {}))
schemas = len(spec.get("components", {}).get("schemas", {}))

if CHECK_MODE:
    # Compare generated spec against committed spec
    if not os.path.exists(output_path):
        print("FAIL: backend/openapi.json does not exist. Run: make openapi")
        sys.exit(1)

    with open(output_path) as f:
        committed = json.load(f)

    # Normalize committed version too
    committed_for_compare = json.loads(json.dumps(committed))
    committed_for_compare["info"]["version"] = "0.0.0"

    if json.dumps(spec_for_compare, sort_keys=True) == json.dumps(committed_for_compare, sort_keys=True):
        print(f"OK: backend/openapi.json is up to date ({paths} paths, {schemas} schemas)")
        sys.exit(0)
    else:
        print("FAIL: backend/openapi.json is stale. Regenerate with: make openapi")
        print()
        # Show a helpful diff summary
        committed_paths = set(committed.get("paths", {}).keys())
        current_paths = set(spec.get("paths", {}).keys())
        committed_schemas = set(committed.get("components", {}).get("schemas", {}).keys())
        current_schemas = set(spec.get("components", {}).get("schemas", {}).keys())

        added_paths = current_paths - committed_paths
        removed_paths = committed_paths - current_paths
        added_schemas = current_schemas - committed_schemas
        removed_schemas = committed_schemas - current_schemas

        if added_paths:
            print(f"  Added paths: {sorted(added_paths)}")
        if removed_paths:
            print(f"  Removed paths: {sorted(removed_paths)}")
        if added_schemas:
            print(f"  Added schemas: {sorted(added_schemas)}")
        if removed_schemas:
            print(f"  Removed schemas: {sorted(removed_schemas)}")
        if not (added_paths or removed_paths or added_schemas or removed_schemas):
            print("  (Schema definitions changed but path/schema names are the same)")
        sys.exit(1)
else:
    # Write to backend/openapi.json
    with open(output_path, "w") as f:
        json.dump(spec, f, indent=2, sort_keys=False)
        f.write("\n")  # trailing newline

    print(f"Generated {output_path}")
    print(f"  Paths: {paths}")
    print(f"  Schemas: {schemas}")
    print(f"  OpenAPI version: {spec.get('openapi', 'unknown')}")
    print(f"  API version: {spec['info']['version']}")
