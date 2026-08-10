import sys
import os
import time
import threading
import asyncio
import httpx
import uvicorn
from fastapi import FastAPI, Depends
from unittest.mock import MagicMock

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

# Mock models
mock_models = MagicMock()
sys.modules['models'] = mock_models

# Import router
from routes import state_viewer
from database import get_db

app = FastAPI()
app.include_router(state_viewer.router)

@app.get("/health")
async def health():
    return {"status": "ok"}

def get_slow_db():
    db = MagicMock()
    def slow_query(*args, **kwargs):
        # This simulates a blocking DB call
        time.sleep(1.0)
        return MagicMock(filter=lambda *a, **k: MagicMock(first=lambda: None))
    db.query = slow_query
    return db

app.dependency_overrides[get_db] = get_slow_db

def run_server():
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="critical")

async def run_benchmark():
    async with httpx.AsyncClient() as client:
        print("Sending slow request...")
        # Start the slow request
        task_slow = asyncio.create_task(client.get("http://127.0.0.1:8001/api/state/module/1"))

        # Give it a tiny bit to reach the server and start blocking
        await asyncio.sleep(0.2)

        print("Sending fast request...")
        # Start fast request
        start = time.time()
        await client.get("http://127.0.0.1:8001/health")
        end = time.time()

        latency = end - start
        print(f"Fast request latency: {latency:.4f}s")

        # Cleanup
        try:
            await asyncio.wait_for(task_slow, timeout=2.0)
        except Exception as e:
            print(f"Slow request finished or timed out: {e}")

        return latency

def main():
    print("Starting benchmark server...")
    t = threading.Thread(target=run_server, daemon=True)
    t.start()
    time.sleep(2)

    print("Running benchmark...")
    latency = asyncio.run(run_benchmark())

    if latency > 0.8:
        print(f"FAIL: Event loop blocked (Latency {latency:.4f}s > 0.8s)")
        # Exit with error code to signal failure
        sys.exit(1)
    else:
        print(f"PASS: Event loop not blocked (Latency {latency:.4f}s < 0.8s)")
        sys.exit(0)

if __name__ == "__main__":
    main()
