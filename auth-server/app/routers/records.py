"""The last record each assistant created. Processing stays on each person's computer; only this one small record per assistant,
with a fixed list of fields and no contact details, is kept here."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import current_user, get_db
from ..models import LastRecord, User, utcnow

router = APIRouter(prefix="/records", tags=["records"])

# The only fields accepted for each assistant. Anything else is refused, so a contact detail cannot ride along by mistake.
ALLOWED: dict[str, set[str]] = {
    "candidate": {"name", "title", "company", "location", "match_score", "profile_url", "jd", "date_found"},
    "company": {"job_title", "company", "location", "status", "source_type", "posted_date", "listing_url", "date_discovered"},
    "account": {"company", "domain", "product_type", "hq", "funding", "confidence", "last_checked"},
}
MAX_VALUE = 300
MAX_URL = 600
MAX_JSON = 3500
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?\d[\d .()-]{7,}\d")


class LastRecordIn(BaseModel):
    record: dict[str, str | int | float | None]
    subject_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime


def _naive_utc(d: datetime) -> datetime:
    return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d


def _check_record(agent: str, record: dict) -> None:
    extra = sorted(set(record) - ALLOWED[agent])
    if extra:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Field not accepted: {', '.join(extra)}")
    for key, value in record.items():
        if not isinstance(value, str):
            continue
        if len(value) > (MAX_URL if key.endswith("_url") else MAX_VALUE):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{key} is too long")
        if _EMAIL.search(value) or (not key.endswith("_url") and any(len(re.sub(r"\D", "", m.group(0))) >= 9 for m in _PHONE.finditer(value))):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Contact details are not accepted")
    if len(json.dumps(record)) > MAX_JSON:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The record is too large")


def _agent(agent: str) -> str:
    if agent not in ALLOWED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such assistant")
    return agent


def _out(row: LastRecord, db: Session) -> dict:
    who = db.get(User, row.stored_by) if row.stored_by else None
    return {"agent": row.agent, "record": json.loads(row.record_json), "subject_hash": row.subject_hash,
            "created_at": row.created_at.isoformat(), "stored_at": row.stored_at.isoformat(), "stored_by": who.email if who else None}


@router.put("/last/{agent}")
def put_last(agent: str, body: LastRecordIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Store the record unless a newer one is already there (an older computer must not overwrite it). Same record again: no change."""
    agent = _agent(agent)
    _check_record(agent, body.record)
    created = _naive_utc(body.created_at)
    row = db.get(LastRecord, (user.tenant_id, agent))
    if row is not None and created < row.created_at:
        raise HTTPException(status.HTTP_409_CONFLICT, "A newer record is already stored")
    if row is None:
        row = LastRecord(tenant_id=user.tenant_id, agent=agent, record_json="{}", subject_hash=body.subject_hash, created_at=created)
        db.add(row)
    if row.subject_hash != body.subject_hash or row.created_at != created or json.loads(row.record_json) != body.record:
        row.record_json = json.dumps(body.record, ensure_ascii=False, sort_keys=True)
        row.subject_hash, row.created_at, row.stored_at, row.stored_by = body.subject_hash, created, utcnow(), user.id
    db.commit()
    return _out(row, db)


@router.get("/last")
def list_last(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [_out(r, db) for r in db.scalars(select(LastRecord).where(LastRecord.tenant_id == user.tenant_id).order_by(LastRecord.agent))]


@router.delete("/last/{agent}", status_code=status.HTTP_204_NO_CONTENT)
def delete_last(agent: str, subject_hash: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Remove the stored record, but only if it is the one the caller means (its hash): a newer record from another computer stays.
    Used when the person a record is about has been erased on the computer that created it."""
    row = db.get(LastRecord, (user.tenant_id, _agent(agent)))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nothing stored")
    if row.subject_hash != subject_hash:
        raise HTTPException(status.HTTP_409_CONFLICT, "A different record is stored")
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
