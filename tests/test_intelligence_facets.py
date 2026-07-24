from app.archive.intelligence_facets import attach_archive_facets
from app.schemas import ArchiveIntelligenceResponse, ArchiveSummary, ArchiveTopicCard


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FacetCatalogDb:
    def execute(self, statement, params):
        sql = str(statement)
        if "FROM archive_people" in sql:
            return _Rows(
                [
                    {
                        "slug": "will-neff",
                        "display_name": "Will Neff",
                        "aliases": ["Will"],
                        "description": None,
                        "default_role": "guest",
                        "sort_order": 0,
                    }
                ]
            )
        if "FROM archive_video_tags" in sql:
            return _Rows(
                [
                    {
                        "slug": "new-york",
                        "label": "New York",
                        "kind": "place",
                        "description": None,
                        "sort_order": 0,
                    }
                ]
            )
        raise AssertionError(sql)


def test_topic_catalog_populates_people_and_tags_without_video_assignments():
    response = ArchiveIntelligenceResponse(
        summary=ArchiveSummary(),
        topic_cards=[
            ArchiveTopicCard(slug="will-neff", label="Will Neff", source="label_assignments"),
            ArchiveTopicCard(slug="new-york", label="New York", source="label_assignments"),
        ],
    )

    enriched = attach_archive_facets(response, db=_FacetCatalogDb())

    assert [person.display_name for person in enriched.people] == ["Will Neff"]
    assert [tag.label for tag in enriched.tags] == ["New York"]
