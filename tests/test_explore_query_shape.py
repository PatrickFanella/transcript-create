from unittest.mock import Mock

from app.archive.intelligence_repository import published_label_cards_for_period


def test_published_label_cards_rank_evidence_per_label():
    result = Mock()
    result.mappings.return_value.all.return_value = []
    db = Mock()
    db.execute.return_value = result

    assert published_label_cards_for_period(db, limit=8) == []

    statement, params = db.execute.call_args.args
    sql = str(statement)
    assert "CROSS JOIN LATERAL" in sql
    assert "a.label_id = l.id" in sql
    assert "l.source IN ('admin', 'seed', 'hybrid')" in sql
    assert "LIMIT :per_label_limit" in sql
    assert params["per_label_limit"] == 8
    assert params["limit"] == 64
