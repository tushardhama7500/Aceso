from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import observability

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def get_metrics(db: Session = Depends(get_db)):
    return observability.get_metrics_summary(db)
