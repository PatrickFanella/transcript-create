from scripts.opensearch_indexer import outbox_actions


def test_outbox_actions_use_external_versions_for_upserts_and_deletes(monkeypatch):
    monkeypatch.setattr("scripts.opensearch_indexer.settings.OPENSEARCH_INDEX_NATIVE", "native-index")
    rows = [
        {
            "source": "native",
            "document_id": 10,
            "operation": "upsert",
            "version": 100,
            "payload": {"id": 10, "text": "new"},
        },
        {
            "source": "native",
            "document_id": 11,
            "operation": "delete",
            "version": 101,
            "payload": None,
        },
    ]

    assert outbox_actions(rows) == [
        {
            "index": {
                "_index": "native-index",
                "_id": 10,
                "version": 100,
                "version_type": "external_gte",
            }
        },
        {"id": 10, "text": "new"},
        {
            "delete": {
                "_index": "native-index",
                "_id": 11,
                "version": 101,
                "version_type": "external_gte",
            }
        },
    ]
