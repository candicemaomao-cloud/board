from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.services.analytics import build_overview
from app.services.auth import require_user
from app.services.goal import build_goal

router = APIRouter()


@router.get("/overview")
def overview(
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    data = build_overview(db, start, end, user_id=user.id)
    data["goal"] = build_goal(db, user_id=user.id)
    return data
