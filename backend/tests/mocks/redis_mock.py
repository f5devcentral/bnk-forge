"""
Redis and cache mock objects for testing.

Provides mocks for:
- Redis client (used by Celery broker/backend)
- CacheService (our cache abstraction layer)

Tests should never connect to a real Redis instance.
"""

from unittest.mock import MagicMock


class MockRedis:
    """
    In-memory Redis mock that stores key-value pairs in a dict.

    Supports basic Redis operations used by the codebase:
    - get/set/delete
    - keys/scan (pattern matching)
    - expire/ttl
    - pipeline (returns self for chaining)
    - pubsub basics

    Usage:
        with patch("redis.Redis", return_value=MockRedis()):
            ...
    """

    def __init__(self):
        self._store: dict[str, bytes | str] = {}
        self._ttls: dict[str, int] = {}

    def get(self, key: str) -> bytes | None:
        val = self._store.get(key)
        if isinstance(val, str):
            return val.encode()
        return val

    def set(self, key: str, value: str | bytes, ex: int | None = None, **kwargs) -> bool:
        self._store[key] = value
        if ex is not None:
            self._ttls[key] = ex
        return True

    def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            if key in self._store:
                del self._store[key]
                self._ttls.pop(key, None)
                count += 1
        return count

    def exists(self, key: str) -> bool:
        return key in self._store

    def keys(self, pattern: str = "*") -> list[str]:
        """Simplified pattern matching — supports trailing * only."""
        if pattern == "*":
            return list(self._store.keys())
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return [k for k in self._store if k.startswith(prefix)]
        return [k for k in self._store if k == pattern]

    def expire(self, key: str, seconds: int) -> bool:
        if key in self._store:
            self._ttls[key] = seconds
            return True
        return False

    def ttl(self, key: str) -> int:
        return self._ttls.get(key, -1)

    def flushdb(self):
        self._store.clear()
        self._ttls.clear()

    def pipeline(self, **kwargs):
        """Return self for chaining (simplified mock)."""
        return MockRedisPipeline(self)

    def pubsub(self, **kwargs):
        return MagicMock()

    def ping(self) -> bool:
        return True

    def info(self, *args) -> dict:
        return {"redis_version": "7.0.0-mock", "used_memory": 0}


class MockRedisPipeline:
    """Mock Redis pipeline that executes commands immediately."""

    def __init__(self, redis: MockRedis):
        self._redis = redis
        self._results: list = []

    def set(self, key: str, value: str | bytes, **kwargs):
        self._redis.set(key, value, **kwargs)
        self._results.append(True)
        return self

    def get(self, key: str):
        self._results.append(self._redis.get(key))
        return self

    def delete(self, *keys: str):
        self._results.append(self._redis.delete(*keys))
        return self

    def expire(self, key: str, seconds: int):
        self._results.append(self._redis.expire(key, seconds))
        return self

    def execute(self) -> list:
        results = self._results.copy()
        self._results.clear()
        return results

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class MockCacheService:
    """
    Mock of core/cache.py CacheService.

    Provides an in-memory cache with the same interface as the real
    CacheService but without Redis dependency.

    Usage:
        with patch("core.cache.cache", MockCacheService()):
            ...
    """

    def __init__(self):
        self._store: dict[str, str] = {}
        self.redis = None  # Match real CacheService interface

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        self._store[key] = value
        return True

    def delete(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
            return True
        return False

    def delete_pattern(self, pattern: str) -> int:
        """Delete keys matching a glob-like pattern (supports trailing * only)."""
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            keys_to_delete = [k for k in self._store if k.startswith(prefix)]
        else:
            keys_to_delete = [k for k in self._store if k == pattern]

        for key in keys_to_delete:
            del self._store[key]
        return len(keys_to_delete)

    def clear(self):
        """Clear all cached entries."""
        self._store.clear()
