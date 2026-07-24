"""Owner-isolated vocabulary management routes."""

import json
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text

from ..db import get_db
from ..exceptions import AuthorizationError
from ..policy import CAP_VOCABULARIES_GLOBAL, capabilities_for_role
from ..schemas import VocabularyCreate, VocabularyResponse, VocabularyTerm
from ..security import get_user_required, get_user_role

router = APIRouter(prefix="/vocabularies", tags=["Vocabularies"])


def _response(row) -> VocabularyResponse:
    return VocabularyResponse(
        id=row["id"],
        name=row["name"],
        terms=[VocabularyTerm(**term) for term in row["terms"]],
        is_global=row["is_global"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _visible_vocabulary(db, vocabulary_id: uuid.UUID, user_id: str):
    return (
        db.execute(
            text("SELECT * FROM user_vocabularies " "WHERE id=:id AND (is_global=true OR user_id=:user_id)"),
            {"id": str(vocabulary_id), "user_id": user_id},
        )
        .mappings()
        .first()
    )


@router.post("", response_model=VocabularyResponse, status_code=status.HTTP_201_CREATED)
def create_vocabulary(
    payload: VocabularyCreate,
    db=Depends(get_db),
    user=Depends(get_user_required),
):
    role = get_user_role(user)
    if payload.is_global and CAP_VOCABULARIES_GLOBAL not in capabilities_for_role(role):
        raise AuthorizationError("Only administrators can create global vocabularies")

    vocabulary_id = uuid.uuid4()
    owner_id = None if payload.is_global else str(user["id"])
    db.execute(
        text(
            "INSERT INTO user_vocabularies (id, user_id, name, terms, is_global) "
            "VALUES (:id, :user_id, :name, CAST(:terms AS jsonb), :is_global)"
        ),
        {
            "id": str(vocabulary_id),
            "user_id": owner_id,
            "name": payload.name,
            "terms": json.dumps([term.model_dump() for term in payload.terms]),
            "is_global": payload.is_global,
        },
    )
    db.commit()
    row = db.execute(text("SELECT * FROM user_vocabularies WHERE id=:id"), {"id": str(vocabulary_id)}).mappings().one()
    return _response(row)


@router.get("", response_model=List[VocabularyResponse])
def list_vocabularies(db=Depends(get_db), user=Depends(get_user_required)):
    rows = (
        db.execute(
            text(
                "SELECT * FROM user_vocabularies " "WHERE is_global=true OR user_id=:user_id ORDER BY created_at DESC"
            ),
            {"user_id": str(user["id"])},
        )
        .mappings()
        .all()
    )
    return [_response(row) for row in rows]


@router.get("/{vocabulary_id}", response_model=VocabularyResponse)
def get_vocabulary(
    vocabulary_id: uuid.UUID,
    db=Depends(get_db),
    user=Depends(get_user_required),
):
    row = _visible_vocabulary(db, vocabulary_id, str(user["id"]))
    if not row:
        raise HTTPException(status_code=404, detail=f"Vocabulary {vocabulary_id} not found")
    return _response(row)


@router.delete("/{vocabulary_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vocabulary(
    vocabulary_id: uuid.UUID,
    db=Depends(get_db),
    user=Depends(get_user_required),
):
    row = _visible_vocabulary(db, vocabulary_id, str(user["id"]))
    if not row:
        raise HTTPException(status_code=404, detail=f"Vocabulary {vocabulary_id} not found")

    role = get_user_role(user)
    if row["is_global"]:
        if CAP_VOCABULARIES_GLOBAL not in capabilities_for_role(role):
            raise AuthorizationError("Only administrators can delete global vocabularies")
        result = db.execute(
            text("DELETE FROM user_vocabularies WHERE id=:id AND is_global=true"),
            {"id": str(vocabulary_id)},
        )
    else:
        result = db.execute(
            text("DELETE FROM user_vocabularies WHERE id=:id AND user_id=:user_id AND is_global=false"),
            {"id": str(vocabulary_id), "user_id": str(user["id"])},
        )
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"Vocabulary {vocabulary_id} not found")
