import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.chapterize.db import get_chapterize_db
from app.chapterize.models import DEFAULT_NAMING_SCHEMA, ChapterizeSettings

router = APIRouter()


class NamingSchema(BaseModel):
    prologue: str = "Prologue"
    opening: str = "Opening"
    ending: str = "Ending"
    epilogue: str = "Epilogue"
    end: str = "End"


class SettingsPayload(BaseModel):
    naming_schema: NamingSchema
    match_threshold: float
    animethemes_cache_ttl_days: int


def _row_to_dict(row: ChapterizeSettings) -> dict:
    try:
        naming_schema = json.loads(row.naming_schema_json)
    except (json.JSONDecodeError, TypeError):
        naming_schema = dict(DEFAULT_NAMING_SCHEMA)
    # The "episode" chapter's title is fixed to "Episode" (see jobs.py) and
    # isn't part of the configurable schema - drop any stale key a
    # pre-existing settings row might still carry from before that.
    naming_schema.pop("episode", None)
    return {
        "naming_schema": naming_schema,
        "match_threshold": row.match_threshold,
        "animethemes_cache_ttl_days": row.animethemes_cache_ttl_days,
    }


@router.get("")
def get_settings(db: Session = Depends(get_chapterize_db)):
    row = db.query(ChapterizeSettings).first()
    if row is None:
        raise HTTPException(status_code=500, detail="Chapter analyzer settings not initialized")
    return _row_to_dict(row)


@router.put("")
def update_settings(payload: SettingsPayload, db: Session = Depends(get_chapterize_db)):
    row = db.query(ChapterizeSettings).first()
    if row is None:
        row = ChapterizeSettings(id=1)
        db.add(row)
    row.naming_schema_json = json.dumps(payload.naming_schema.model_dump())
    row.match_threshold = payload.match_threshold
    row.animethemes_cache_ttl_days = payload.animethemes_cache_ttl_days
    db.commit()
    return _row_to_dict(row)
