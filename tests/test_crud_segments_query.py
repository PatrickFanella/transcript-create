import inspect
from unittest.mock import Mock

from app import crud


def _result(rows):
    result = Mock()
    result.all.return_value = rows
    return result


def test_list_segments_returns_direct_rows_without_legacy_scan():
    db = Mock()
    db.execute.return_value = _result([(0, 1000, "hello", None)])

    rows = inspect.unwrap(crud.list_segments)(db, "video-1")

    assert rows == [(0, 1000, "hello", None)]
    assert db.execute.call_count == 1
    assert "WHERE s.video_id = :v" in str(db.execute.call_args.args[0])


def test_list_segments_uses_legacy_relationship_only_when_direct_rows_are_empty():
    db = Mock()
    db.execute.side_effect = [_result([]), _result([(1000, 2000, "legacy", None)])]

    rows = inspect.unwrap(crud.list_segments)(db, "video-1")

    assert rows == [(1000, 2000, "legacy", None)]
    assert db.execute.call_count == 2
    assert "JOIN transcripts" in str(db.execute.call_args_list[1].args[0])
