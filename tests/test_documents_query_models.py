from __future__ import annotations

import pytest
from pydantic import ValidationError

from matlock.query_models import DocumentQueryResponse, DocumentRecord


def _document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "file_path": "Notes/demo.md",
        "absolute_path": "/tmp/vault/Notes/demo.md",
        "file_ext": ".md",
        "created": "2026-01-01T00:00:00Z",
        "modified": "2026-01-02T00:00:00Z",
        "modified_date": "2026-01-02",
        "length": 120,
        "word_count": 20,
        "project_ids": ["proj-1"],
        "project_id": "proj-1",
        "super_project_id": "super-1",
        "score": 1.0,
        "file_details": {"total_matching_chunks": 1, "content": None},
    }
    document.update(overrides)
    return document


def test_document_record_supports_file_level_metadata() -> None:
    record = DocumentRecord.model_validate(_document())

    assert record.file_path == "Notes/demo.md"
    assert record.length == 120
    assert record.project_ids == ["proj-1"]
    assert record.file_details is not None


def test_document_record_supports_chunk_level_details() -> None:
    record = DocumentRecord.model_validate(
        _document(
            file_details=None,
            chunk_details={
                "chunk_id": "Notes/demo.md#0000",
                "chunk_index": 0,
                "total_chunks": 2,
                "content": "safe content",
            },
        )
    )

    assert record.chunk_details is not None
    assert record.chunk_details.chunk_index == 0
    assert record.file_details is None


def test_document_record_rejects_both_detail_shapes() -> None:
    with pytest.raises(ValidationError, match="both chunk_details and file_details"):
        DocumentRecord.model_validate(
            _document(
                chunk_details={
                    "chunk_id": "chunk-1",
                    "chunk_index": 0,
                    "total_chunks": 1,
                }
            )
        )


def test_document_query_response_supports_count_only() -> None:
    response = DocumentQueryResponse(
        total_matches=4,
        returned_matches=0,
        limit=20,
        offset=0,
        results=[],
    )

    assert response.total_matches == 4
    assert response.results == []