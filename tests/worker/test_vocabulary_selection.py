from worker.vocabulary import load_vocabularies


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _Connection:
    def __init__(self, rows=None):
        self.calls = []
        self.rows = rows

    def execute(self, statement, params):
        self.calls.append((str(statement), params))
        rows = self.rows
        if rows is None:
            rows = [{"id": params["vocabulary_ids"][0], "name": "Selected", "terms": []}]
        return _Result(rows)


def test_worker_loads_exactly_selected_accessible_vocabularies():
    connection = _Connection()

    rows = load_vocabularies(connection, "owner-1", ["vocab-2"])

    assert [row["id"] for row in rows] == ["vocab-2"]
    assert len(connection.calls) == 1
    sql, params = connection.calls[0]
    assert "is_global = true OR user_id = :user_id" in sql
    assert params == {"vocabulary_ids": ["vocab-2"], "user_id": "owner-1"}


def test_worker_loads_nothing_when_job_selected_no_vocabularies():
    connection = _Connection()

    assert load_vocabularies(connection, "owner-1", []) == []
    assert connection.calls == []


def test_worker_fails_if_a_selected_vocabulary_disappeared():
    connection = _Connection(rows=[])

    try:
        load_vocabularies(connection, "owner-1", ["vocab-deleted"])
    except ValueError as error:
        assert "unavailable" in str(error)
    else:
        raise AssertionError("missing selected vocabulary must fail the worker stage")
