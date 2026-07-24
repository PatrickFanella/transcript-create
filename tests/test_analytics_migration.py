"""Contract checks for the additive analytics migration and guarded rollout."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic/versions/20260710_2200_add_analytics_subject_identity.py"
INDEX_MIGRATION = ROOT / "alembic/versions/20260710_2210_add_analytics_subject_index.py"
SCRUB_SCRIPT = ROOT / "scripts/finalize_analytics_identity.sh"
RETENTION_SCRIPT = ROOT / "scripts/maintain_event_retention.sh"
RETENTION_COMMAND = ROOT / "scripts/maintain_event_retention.py"


def test_analytics_identity_migration_is_additive_and_current_head() -> None:
    spec = importlib.util.spec_from_file_location("analytics_identity_migration", MIGRATION)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.down_revision == "20260605_topic_policy"
    source = MIGRATION.read_text()
    assert "analytics_subject_id" in source
    assert "event_daily_aggregates" in source
    assert "drop_column" not in source.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert "session_token" not in source

    index_source = INDEX_MIGRATION.read_text()
    assert 'down_revision: Union[str, None] = "20260710_analytics_identity"' in index_source
    assert "DROP INDEX CONCURRENTLY IF EXISTS events_analytics_subject_idx" in index_source
    assert "CREATE INDEX CONCURRENTLY events_analytics_subject_idx" in index_source
    assert index_source.index("DROP INDEX CONCURRENTLY") < index_source.index("CREATE INDEX CONCURRENTLY")
    assert "DROP INDEX CONCURRENTLY IF EXISTS events_analytics_subject_idx" in index_source
    assert "autocommit_block()" in index_source


def test_fresh_schema_uses_subject_identity_without_legacy_event_token() -> None:
    schema = (ROOT / "sql/schema.sql").read_text()
    event_table = schema.split("CREATE TABLE IF NOT EXISTS events (", 1)[1].split(");", 1)[0]

    assert "analytics_subject_id CHAR(64)" in event_table
    assert "session_token" not in event_table
    assert "CREATE INDEX IF NOT EXISTS events_analytics_subject_idx" in schema
    assert "CREATE TABLE IF NOT EXISTS event_daily_aggregates" in schema


def test_post_deploy_scrub_is_guarded_transactional_and_idempotent() -> None:
    script = SCRUB_SCRIPT.read_text()

    assert "CONFIRM_ANALYTICS_CREDENTIAL_ROTATION" in script
    assert "ON_ERROR_STOP=1" in script
    assert "pg_advisory_xact_lock" in script
    assert "BEGIN;" in script and "COMMIT;" in script
    assert "AT TIME ZONE 'UTC'" in script
    assert "ON CONFLICT (day, type) DO UPDATE" in script
    assert "UPDATE events SET session_token = NULL" in script
    assert "DELETE FROM sessions" in script
    assert "CREATE OR REPLACE FUNCTION null_legacy_event_session_token" in script
    assert "DROP TRIGGER IF EXISTS" in script
    assert "DROP COLUMN" not in script


def test_retention_maintenance_aggregates_before_deleting_raw_events() -> None:
    wrapper = RETENTION_SCRIPT.read_text()
    script = RETENTION_COMMAND.read_text()

    assert "maintain_event_retention.py" in wrapper
    assert "with database_engine.begin()" in script
    assert "AT TIME ZONE 'UTC'" in script
    assert "ON CONFLICT (day, type) DO UPDATE" in script
    assert "event_daily_aggregates.count + EXCLUDED.count" in script
    assert script.index("INSERT INTO event_daily_aggregates") < script.index("DELETE FROM events")
    assert "CURRENT_TIMESTAMP - INTERVAL '90 days'" in script


def test_retention_command_is_scheduled_in_compose_and_kubernetes() -> None:
    compose = (ROOT / "docker-compose.yml").read_text()
    compose_prod = (ROOT / "docker-compose.prod.yml").read_text()
    kubernetes = (ROOT / "k8s/analytics-retention-cronjob.yaml").read_text()
    helm = (ROOT / "charts/transcript-create/templates/analytics-retention-cronjob.yaml").read_text()
    values = (ROOT / "charts/transcript-create/values.yaml").read_text()

    assert "analytics-retention:" in compose
    assert "maintain_event_retention.py" in compose
    assert "RETENTION_INTERVAL_SECONDS" in compose_prod
    assert "kind: CronJob" in kubernetes
    assert 'schedule: "17 3 * * *"' in kubernetes
    assert "concurrencyPolicy: Forbid" in kubernetes
    assert "maintain_event_retention.py" in kubernetes
    assert ".Values.analyticsRetention.enabled" in helm
    assert "analyticsRetention:" in values
