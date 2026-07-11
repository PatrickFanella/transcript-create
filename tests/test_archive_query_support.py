from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from app.archive.query_support import query_mappings
from app.exceptions import ArchiveUnavailableError


def test_archive_database_failure_is_unavailable_not_empty(monkeypatch):
    db = MagicMock()
    db.execute.side_effect = OperationalError("select", {}, Exception("connection lost"))
    metric = MagicMock()
    monkeypatch.setattr("app.metrics.archive_unavailable_total", metric)

    with pytest.raises(ArchiveUnavailableError) as error:
        query_mappings(db, "SELECT 1")

    assert error.value.status_code == 503
    db.rollback.assert_called_once()
    metric.labels.assert_called_once_with(operation="query_many")
