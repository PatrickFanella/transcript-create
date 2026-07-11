"""Tests for caching functionality."""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.cache import (
    cache,
    clear_all_cache,
    generate_cache_key,
    get_cache_stats,
    invalidate_cache,
    invalidate_cache_pattern,
    invalidate_video_data,
)


class TestCacheKeyGeneration:
    """Tests for cache key generation."""

    def test_generate_cache_key_simple(self):
        """Test generating cache key with simple arguments."""
        key = generate_cache_key("video", "123")
        assert key == "video:123"

    def test_generate_cache_key_multiple_args(self):
        """Test generating cache key with multiple arguments."""
        key = generate_cache_key("segments", "abc-def", "start_ms=1000")
        assert key == "segments:abc-def:start_ms=1000"

    def test_generate_cache_key_with_kwargs(self):
        """Test generating cache key with keyword arguments."""
        key = generate_cache_key("search", query="test", limit=50, offset=0)
        assert "search:" in key
        assert "limit=50" in key or len(key) < 100  # May be hashed if too long

    def test_generate_cache_key_long_string_hashed(self):
        """Test that long cache keys are hashed."""
        long_string = "x" * 150
        key = generate_cache_key("test", long_string)
        # Should be hashed to keep it short
        assert len(key) < 50


class TestCacheDecorator:
    """Tests for cache decorator functionality."""

    @patch("app.cache.get_redis_client")
    def test_cache_decorator_miss(self, mock_get_redis):
        """Test cache decorator on cache miss."""
        # Mock Redis client
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_get_redis.return_value = mock_redis

        @cache(prefix="test", ttl=300)
        def test_function(arg):
            return f"result_{arg}"

        result = test_function("value")

        assert result == "result_value"
        mock_redis.get.assert_called_once()
        mock_redis.setex.assert_called_once()

    @patch("app.cache.get_redis_client")
    def test_cache_decorator_hit(self, mock_get_redis):
        """Test cache decorator on cache hit."""
        # Mock Redis client
        mock_redis = MagicMock()
        mock_redis.get.return_value = json.dumps({"version": 1, "value": "cached_result"})
        mock_get_redis.return_value = mock_redis

        call_count = 0

        @cache(prefix="test", ttl=300)
        def test_function(arg):
            nonlocal call_count
            call_count += 1
            return f"result_{arg}"

        result = test_function("value")

        assert result == "cached_result"
        assert call_count == 0  # Function should not be called
        mock_redis.get.assert_called_once()
        mock_redis.setex.assert_not_called()

    @patch("app.cache.get_redis_client")
    def test_cache_uses_versioned_json_and_cold_warm_results_match(self, mock_get_redis):
        stored = {}
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda key: stored.get(key)
        mock_redis.setex.side_effect = lambda key, _ttl, value: stored.__setitem__(key, value)
        mock_get_redis.return_value = mock_redis
        value_id = uuid.uuid4()
        created_at = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)

        @cache(prefix="parity", ttl=300)
        def test_function():
            return {"id": value_id, "created_at": created_at, "items": [(1, "two")]}

        cold = test_function()
        warm = test_function()

        assert (
            cold
            == warm
            == {
                "id": str(value_id),
                "created_at": created_at.isoformat(),
                "items": [[1, "two"]],
            }
        )
        cache_key = next(iter(stored))
        assert ":v1:" in cache_key
        assert json.loads(stored[cache_key]) == {"version": 1, "value": cold}

    @patch("app.cache.get_redis_client")
    def test_cache_discards_unversioned_legacy_values(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_redis.get.return_value = json.dumps({"legacy": True})
        mock_get_redis.return_value = mock_redis

        @cache(prefix="legacy", ttl=300)
        def test_function():
            return {"current": True}

        assert test_function() == {"current": True}
        mock_redis.delete.assert_called_once()
        mock_redis.setex.assert_called_once()

    @patch("app.cache.get_redis_client")
    def test_cache_decorator_no_redis(self, mock_get_redis):
        """Test cache decorator when Redis is unavailable."""
        mock_get_redis.return_value = None

        @cache(prefix="test", ttl=300)
        def test_function(arg):
            return f"result_{arg}"

        result = test_function("value")

        assert result == "result_value"

    @patch("app.cache.get_redis_client")
    def test_cache_decorator_skip_none(self, mock_get_redis):
        """Test cache decorator skips None results by default."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_get_redis.return_value = mock_redis

        @cache(prefix="test", ttl=300)
        def test_function():
            return None

        result = test_function()

        assert result is None
        mock_redis.setex.assert_not_called()

    @patch("app.cache.get_redis_client")
    def test_cache_decorator_cache_none(self, mock_get_redis):
        """Test cache decorator caches None when skip_none=False."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_get_redis.return_value = mock_redis

        @cache(prefix="test", ttl=300, skip_none=False)
        def test_function():
            return None

        result = test_function()

        assert result is None
        mock_redis.setex.assert_called_once()


class TestCacheInvalidation:
    """Tests for cache invalidation."""

    @patch("app.cache.get_redis_client")
    def test_invalidate_cache(self, mock_get_redis):
        """Test invalidating a specific cache key."""
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        invalidate_cache("video", "123")

        mock_redis.delete.assert_called_once()

    @patch("app.cache.get_redis_client")
    def test_invalidate_cache_pattern(self, mock_get_redis):
        """Test invalidating cache keys by pattern."""
        mock_redis = MagicMock()
        mock_redis.scan_iter.return_value = ["video:1", "video:2", "video:3"]
        mock_get_redis.return_value = mock_redis

        invalidate_cache_pattern("video:*")

        assert mock_redis.delete.call_count == 3

    @patch("app.cache.invalidate_cache_pattern")
    @patch("app.cache.invalidate_cache")
    def test_video_invalidation_covers_all_derived_dtos(self, mock_invalidate, mock_pattern):
        invalidate_video_data("video-123")

        assert mock_invalidate.call_args_list == [
            (("video", "video-123"),),
            (("segments", "video-123"),),
        ]
        assert [call.args[0] for call in mock_pattern.call_args_list] == [
            "search:*",
            "archive:*",
            "aggregate:*",
        ]

    @patch("app.cache.get_redis_client")
    def test_clear_all_cache(self, mock_get_redis):
        """Test clearing all cache."""
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        clear_all_cache()

        mock_redis.flushdb.assert_called_once()

    @patch("app.cache.get_redis_client")
    def test_invalidate_cache_no_redis(self, mock_get_redis):
        """Test cache invalidation when Redis is unavailable."""
        mock_get_redis.return_value = None

        # Should not raise an error
        invalidate_cache("video", "123")
        invalidate_cache_pattern("video:*")
        clear_all_cache()


class TestCacheStats:
    """Tests for cache statistics."""

    @patch("app.cache.get_redis_client")
    def test_get_cache_stats(self, mock_get_redis):
        """Test getting cache statistics."""
        mock_redis = MagicMock()
        mock_redis.info.return_value = {
            "used_memory_human": "1.5M",
            "connected_clients": 5,
            "uptime_in_seconds": 3600,
            "keyspace_hits": 100,
            "keyspace_misses": 20,
        }
        mock_redis.dbsize.return_value = 42
        mock_get_redis.return_value = mock_redis

        stats = get_cache_stats()

        assert stats["available"] is True
        assert stats["used_memory"] == "1.5M"
        assert stats["total_keys"] == 42
        assert stats["connected_clients"] == 5
        assert stats["hit_rate"] == pytest.approx(100 / 120)

    @patch("app.cache.get_redis_client")
    def test_get_cache_stats_no_redis(self, mock_get_redis):
        """Test getting cache stats when Redis is unavailable."""
        mock_get_redis.return_value = None

        stats = get_cache_stats()

        assert stats["available"] is False
