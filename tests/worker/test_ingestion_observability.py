"""Tests for ingestion observability: structured logs and metrics."""

import logging
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from worker.youtube_resilience import reset_circuit_breakers_for_tests


@pytest.fixture(autouse=True)
def _isolate_ytdlp_settings(monkeypatch):
    """Prevent observability tests from touching mounted runtime cookies."""
    monkeypatch.setattr("worker.audio.settings.YTDLP_COOKIES_PATH", None)
    monkeypatch.setattr("worker.youtube_captions.settings.YTDLP_COOKIES_PATH", None)
    reset_circuit_breakers_for_tests()
    yield
    reset_circuit_breakers_for_tests()


class TestYtdlpMetrics:
    """Tests for yt-dlp operation metrics."""

    def test_ytdlp_metrics_exist(self):
        """Test that yt-dlp observability metrics are defined."""
        from worker.metrics import (
            ytdlp_operation_attempts_total,
            ytdlp_operation_duration_seconds,
            ytdlp_operation_errors_total,
            ytdlp_token_usage_total,
        )

        # Verify metrics are defined
        assert ytdlp_operation_duration_seconds is not None
        assert ytdlp_operation_attempts_total is not None
        assert ytdlp_operation_errors_total is not None
        assert ytdlp_token_usage_total is not None

        # Verify labels are correct
        assert ytdlp_operation_duration_seconds._labelnames == ("operation", "client")
        assert ytdlp_operation_attempts_total._labelnames == ("operation", "client", "result")
        assert ytdlp_operation_errors_total._labelnames == ("operation", "client", "error_class")
        assert ytdlp_token_usage_total._labelnames == ("operation", "has_token")


class TestDownloadAudioObservability:
    """Tests for download_audio observability."""

    @patch("worker.audio.subprocess.run")
    @patch("worker.audio._get_po_tokens")
    def test_download_logs_structured_fields(self, mock_get_tokens, mock_run, tmp_path, caplog):
        """Test that download operations log structured fields."""
        from worker.audio import download_audio

        url = "https://www.youtube.com/watch?v=test123"
        dest_dir = tmp_path / "test_video"
        dest_dir.mkdir()

        # Mock successful subprocess run
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        mock_get_tokens.return_value = {"player": "mock_token"}

        target_logger = logging.getLogger("worker.audio")
        previous_level = target_logger.level
        previous_disabled = target_logger.disabled
        target_logger.setLevel(logging.INFO)
        target_logger.disabled = False
        target_logger.addHandler(caplog.handler)
        try:
            with caplog.at_level("INFO"):
                download_audio(url, dest_dir)
        finally:
            target_logger.removeHandler(caplog.handler)
            target_logger.setLevel(previous_level)
            target_logger.disabled = previous_disabled

        # Verify structured log fields are present
        log_records = [r for r in caplog.records if "download" in r.getMessage().lower()]
        assert len(log_records) > 0

        # Check that at least one log has structured fields
        found_structured = False
        for record in log_records:
            if hasattr(record, "operation") or "operation" in getattr(record, "extra", {}):
                found_structured = True
                break

        # Also check message content for key fields
        log_messages = " ".join([r.getMessage() for r in log_records])
        assert "client" in log_messages.lower() or found_structured

    @patch("worker.audio.subprocess.run")
    @patch("worker.audio._get_po_tokens")
    def test_download_increments_metrics_on_success(self, mock_get_tokens, mock_run, tmp_path):
        """Test that successful downloads increment success metrics."""
        from worker.audio import _build_client_strategies, download_audio
        from worker.metrics import ytdlp_operation_attempts_total

        url = "https://www.youtube.com/watch?v=test123"
        dest_dir = tmp_path / "test_video"
        dest_dir.mkdir()

        # Mock successful subprocess run
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        mock_get_tokens.return_value = {}

        client_name = _build_client_strategies()[0].name

        # Get metric values before
        before_attempts = ytdlp_operation_attempts_total.labels(
            operation="download", client=client_name, result="success"
        )._value.get()

        download_audio(url, dest_dir)

        # Get metric values after
        after_attempts = ytdlp_operation_attempts_total.labels(
            operation="download", client=client_name, result="success"
        )._value.get()

        # Verify metrics increased
        assert after_attempts > before_attempts

    @patch("worker.audio.subprocess.run")
    @patch("worker.audio._get_po_tokens")
    def test_download_increments_error_metrics_on_failure(self, mock_get_tokens, mock_run, tmp_path):
        """Test that failed downloads increment failure metrics."""
        from worker.audio import _build_client_strategies, download_audio
        from worker.metrics import ytdlp_operation_attempts_total

        url = "https://www.youtube.com/watch?v=test123"
        dest_dir = tmp_path / "test_video"
        dest_dir.mkdir()

        # Mock failed subprocess run
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["yt-dlp"], stderr="ERROR: Video unavailable"
        )
        mock_get_tokens.return_value = {}

        client_name = _build_client_strategies()[0].name

        # Get metric values before
        before_failures = ytdlp_operation_attempts_total.labels(
            operation="download", client=client_name, result="failure"
        )._value.get()

        # Attempt download (should fail)
        with pytest.raises(subprocess.CalledProcessError):
            download_audio(url, dest_dir)

        # Get metric values after
        after_failures = ytdlp_operation_attempts_total.labels(
            operation="download", client=client_name, result="failure"
        )._value.get()

        # Verify failure metrics increased
        assert after_failures > before_failures

    @patch("worker.audio.subprocess.run")
    @patch("worker.audio._get_po_tokens")
    def test_download_tracks_token_usage(self, mock_get_tokens, mock_run, tmp_path):
        """Test that token usage is tracked in metrics."""
        from worker.audio import download_audio
        from worker.metrics import ytdlp_token_usage_total

        url = "https://www.youtube.com/watch?v=test123"
        dest_dir = tmp_path / "test_video"
        dest_dir.mkdir()

        # Mock successful subprocess run with token
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        mock_get_tokens.return_value = {"player": "mock_token"}

        # Get metric values before
        before_with_token = ytdlp_token_usage_total.labels(operation="download", has_token="true")._value.get()

        download_audio(url, dest_dir)

        # Get metric values after
        after_with_token = ytdlp_token_usage_total.labels(operation="download", has_token="true")._value.get()

        # Verify token usage was tracked
        assert after_with_token > before_with_token


class TestMetadataFetchObservability:
    """Tests for metadata fetch observability."""

    @patch("worker.youtube_captions.YtDlpExecutor")
    @patch("worker.youtube_captions._get_subs_token")
    def test_metadata_logs_structured_fields(self, mock_get_token, mock_run, caplog):
        """Test that metadata fetch logs structured fields."""
        from worker.youtube_captions import _yt_dlp_json

        url = "https://www.youtube.com/watch?v=test123"

        mock_run.return_value.run_json.return_value = {"id": "test123", "title": "Test"}
        mock_get_token.return_value = None

        target_logger = logging.getLogger("worker.youtube_captions")
        previous_level = target_logger.level
        previous_disabled = target_logger.disabled
        target_logger.setLevel(logging.INFO)
        target_logger.disabled = False
        target_logger.addHandler(caplog.handler)
        try:
            with caplog.at_level("INFO"):
                _yt_dlp_json(url)
        finally:
            target_logger.removeHandler(caplog.handler)
            target_logger.setLevel(previous_level)
            target_logger.disabled = previous_disabled

        # Verify structured log fields are present
        log_records = [r for r in caplog.records if "metadata" in r.getMessage().lower()]
        assert len(log_records) > 0

        # Check for key fields in logs
        log_messages = " ".join([r.getMessage() for r in log_records])
        assert "client" in log_messages.lower() or "operation" in log_messages.lower()

    @patch("worker.youtube_captions.YtDlpExecutor")
    @patch("worker.youtube_captions._get_subs_token")
    def test_metadata_increments_metrics_on_success(self, mock_get_token, mock_run):
        """Test that successful metadata fetch increments metrics."""
        from worker.metrics import ytdlp_operation_attempts_total
        from worker.youtube_captions import _build_metadata_strategies, _yt_dlp_json

        url = "https://www.youtube.com/watch?v=test123"

        mock_run.return_value.run_json.return_value = {"id": "test123", "title": "Test"}
        mock_get_token.return_value = None

        client_name = _build_metadata_strategies()[0][0]

        # Get metric values before - use first strategy client
        before_attempts = ytdlp_operation_attempts_total.labels(
            operation="metadata", client=client_name, result="success"
        )._value.get()

        _yt_dlp_json(url)

        # Get metric values after
        after_attempts = ytdlp_operation_attempts_total.labels(
            operation="metadata", client=client_name, result="success"
        )._value.get()

        # Verify metrics increased
        assert after_attempts > before_attempts


class TestCircuitBreakerMetrics:
    """Tests for circuit breaker metrics."""

    def test_circuit_breaker_state_transitions_update_metrics(self):
        """Test that circuit breaker state transitions update metrics."""
        from worker.metrics import youtube_circuit_breaker_state, youtube_circuit_breaker_transitions_total
        from worker.youtube_resilience import CircuitBreaker

        # Create a new circuit breaker
        breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=1, name="test_breaker")

        # Force a transition by recording failures
        from worker.youtube_resilience import ErrorClass

        breaker._record_failure(ErrorClass.NETWORK)
        breaker._record_failure(ErrorClass.NETWORK)

        # Verify state changed to OPEN (value=2)
        open_state = youtube_circuit_breaker_state.labels(name="test_breaker")._value.get()
        assert open_state == 2

        # Verify transition counter increased
        transitions = youtube_circuit_breaker_transitions_total.labels(
            name="test_breaker", from_state="closed", to_state="open"
        )._value.get()
        assert transitions >= 1


class TestTokenRedaction:
    """Tests for token redaction in logs."""

    def test_redact_tokens_from_command(self):
        """Test that PO tokens are redacted from commands."""
        from worker.token_utils import redact_tokens_from_command

        # Test with quoted token
        cmd = ["yt-dlp", "--extractor-args", "youtube:po_token=player:secret123"]
        redacted = redact_tokens_from_command(cmd)
        assert "secret123" not in redacted
        assert "***REDACTED***" in redacted
        assert "po_token=player:" in redacted

        # Test with multiple tokens
        cmd = ["yt-dlp", "--extractor-args", "youtube:po_token=player:token1;po_token=gvs:token2"]
        redacted = redact_tokens_from_command(cmd)
        assert "token1" not in redacted
        assert "token2" not in redacted
        assert "***REDACTED***" in redacted

    @patch("worker.audio.subprocess.run")
    @patch("worker.audio._get_po_tokens")
    def test_download_logs_do_not_leak_tokens(self, mock_get_tokens, mock_run, tmp_path, caplog):
        """Test that download logs do not contain actual token values."""
        from worker.audio import download_audio

        url = "https://www.youtube.com/watch?v=test123"
        dest_dir = tmp_path / "test_video"
        dest_dir.mkdir()

        # Mock successful subprocess run with token
        mock_token = "super_secret_token_12345"
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        mock_get_tokens.return_value = {"player": mock_token}

        with caplog.at_level("INFO"):
            download_audio(url, dest_dir)

        # Verify token value is not in logs
        all_logs = " ".join([r.getMessage() for r in caplog.records])
        assert mock_token not in all_logs

        # Verify redaction marker is present
        if "po_token" in all_logs.lower():
            assert "REDACTED" in all_logs or "***" in all_logs
