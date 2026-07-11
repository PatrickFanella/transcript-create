"""Redis caching layer for API responses and database queries.

This module provides:
- Redis connection management
- Cache decorators for functions
- Cache key generation
- TTL-based expiration
- Cache invalidation helpers
"""

import functools
import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Callable, Optional

from pydantic import BaseModel

from app.logging_config import get_logger
from app.settings import settings

logger = get_logger(__name__)

# Redis client singleton
_redis_client = None
CACHE_DTO_VERSION = 1


def _to_json_dto(value):
    """Convert supported response values to deterministic JSON primitives."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("Cache DTO mappings require string keys")
        return {key: _to_json_dto(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json_dto(item) for item in value]
    raise TypeError(f"Unsupported cache DTO value: {type(value).__name__}")


def get_redis_client():
    """Get or create Redis client connection."""
    global _redis_client

    if _redis_client is None and settings.REDIS_URL:
        try:
            import redis

            _redis_client = redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            # Test connection
            _redis_client.ping()
            logger.info("Redis client initialized", extra={"redis_url": settings.REDIS_URL.split("@")[-1]})
        except ImportError:
            logger.warning("redis package not installed. Caching disabled.")
            _redis_client = None
        except Exception as e:
            logger.error("Failed to connect to Redis", extra={"error": str(e)})
            _redis_client = None

    return _redis_client


def generate_cache_key(prefix: str, *args, **kwargs) -> str:
    """Generate a cache key from function arguments.

    Args:
        prefix: Cache key prefix (e.g., 'video', 'segments', 'search')
        *args: Positional arguments to include in key
        **kwargs: Keyword arguments to include in key

    Returns:
        Cache key string
    """
    # Create a deterministic string representation of args
    key_parts = [str(arg) for arg in args]
    # Add sorted kwargs for deterministic ordering
    key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
    key_string = ":".join(key_parts)

    # Hash long keys to keep them manageable
    if len(key_string) > 100:
        key_hash = hashlib.sha256(key_string.encode()).hexdigest()[:16]
        return f"{prefix}:{key_hash}"

    return f"{prefix}:{key_string}"


def cache(
    prefix: str,
    ttl: int = 300,
    key_func: Optional[Callable] = None,
    skip_none: bool = True,
):
    """Decorator to cache function results in Redis.

    Args:
        prefix: Cache key prefix
        ttl: Time to live in seconds
        key_func: Optional function to generate cache key from arguments
        skip_none: If True, don't cache None results

    Usage:
        @cache(prefix="video", ttl=300)
        def get_video(video_id: str):
            ...
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            redis_client = get_redis_client()

            if redis_client is None:
                return _to_json_dto(func(*args, **kwargs))

            # Generate cache key
            if key_func:
                cache_key = f"v{CACHE_DTO_VERSION}:{key_func(*args, **kwargs)}"
            else:
                # Skip 'self' or 'db' parameter for instance/DB methods
                is_method = args and (hasattr(args[0], "__dict__") or str(type(args[0])).find("Session") >= 0)
                cache_args = args[1:] if is_method else args
                cache_key = generate_cache_key(f"{prefix}:v{CACHE_DTO_VERSION}", *cache_args, **kwargs)

            try:
                # Try to get from cache
                cached = redis_client.get(cache_key)
                if cached is not None:
                    envelope = json.loads(cached)
                    if (
                        isinstance(envelope, dict)
                        and envelope.get("version") == CACHE_DTO_VERSION
                        and "value" in envelope
                    ):
                        logger.debug("Cache hit", extra={"key": cache_key})
                        if settings.ENABLE_METRICS:
                            from app.metrics import cache_hits_total

                            cache_hits_total.labels(cache_type=prefix).inc()
                        return envelope["value"]
                    redis_client.delete(cache_key)

                logger.debug("Cache miss", extra={"key": cache_key})
                # Track cache miss metric
                if settings.ENABLE_METRICS:
                    from app.metrics import cache_misses_total

                    cache_misses_total.labels(cache_type=prefix).inc()

            except Exception as e:
                logger.warning("Cache read error", extra={"key": cache_key, "error": str(e)})

            # Call the actual function
            result = _to_json_dto(func(*args, **kwargs))

            # Cache the result
            if ttl > 0 and (result is not None or not skip_none):
                try:
                    envelope = {"version": CACHE_DTO_VERSION, "value": result}
                    redis_client.setex(cache_key, ttl, json.dumps(envelope, separators=(",", ":")))
                    logger.debug("Cached result", extra={"key": cache_key, "ttl": ttl})
                except Exception as e:
                    logger.warning("Cache write error", extra={"key": cache_key, "error": str(e)})

            return result

        return wrapper

    return decorator


def invalidate_cache(prefix: str, *args, **kwargs):
    """Invalidate a specific cache entry.

    Args:
        prefix: Cache key prefix
        *args: Arguments used to generate the original cache key
        **kwargs: Keyword arguments used to generate the original cache key
    """
    redis_client = get_redis_client()
    if redis_client is None:
        return

    cache_key = generate_cache_key(f"{prefix}:v{CACHE_DTO_VERSION}", *args, **kwargs)
    try:
        redis_client.delete(cache_key)
        logger.debug("Cache invalidated", extra={"key": cache_key})
    except Exception as e:
        logger.warning("Cache invalidation error", extra={"key": cache_key, "error": str(e)})


def invalidate_cache_pattern(pattern: str):
    """Invalidate all cache entries matching a pattern.

    Args:
        pattern: Redis key pattern (e.g., 'video:*', 'segments:abc123*')

    Warning: This uses SCAN which is more efficient than KEYS but can still
    be expensive on large datasets. Use with caution.
    """
    redis_client = get_redis_client()
    if redis_client is None:
        return

    try:
        count = 0
        for key in redis_client.scan_iter(match=pattern, count=100):
            redis_client.delete(key)
            count += 1
        logger.info("Cache pattern invalidated", extra={"pattern": pattern, "count": count})
    except Exception as e:
        logger.warning("Cache pattern invalidation error", extra={"pattern": pattern, "error": str(e)})


def clear_all_cache():
    """Clear all cache entries. Use with extreme caution!"""
    redis_client = get_redis_client()
    if redis_client is None:
        return

    try:
        redis_client.flushdb()
        logger.warning("All cache cleared")
    except Exception as e:
        logger.error("Failed to clear cache", extra={"error": str(e)})


def get_cache_stats() -> dict:
    """Get Redis cache statistics.

    Returns:
        Dictionary with cache stats (keys, memory, hit rate, etc.)
    """
    redis_client = get_redis_client()
    if redis_client is None:
        return {"available": False}

    try:
        info = redis_client.info()
        return {
            "available": True,
            "used_memory": info.get("used_memory_human"),
            "total_keys": redis_client.dbsize(),
            "connected_clients": info.get("connected_clients"),
            "uptime_seconds": info.get("uptime_in_seconds"),
            "hit_rate": (
                (
                    0.0
                    if (info.get("keyspace_hits", 0) + info.get("keyspace_misses", 0)) == 0
                    else info.get("keyspace_hits", 0) / (info.get("keyspace_hits", 0) + info.get("keyspace_misses", 0))
                )
                if "keyspace_hits" in info
                else None
            ),
        }
    except Exception as e:
        logger.error("Failed to get cache stats", extra={"error": str(e)})
        return {"available": False, "error": str(e)}
