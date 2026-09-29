from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.services.auth import require_user
from app.services.goal import build_goal, get_settings

router = APIRouter()


class SettingsUpdate(BaseModel):
    account_a: float | None = None
    account_b: float | None = None
    current_pnl: float | None = None
    target_profit: float | None = None
    monthly_return: float | None = None
    weekly_plan: str | None = None
    watchlist: str | None = None
    binance_api_key: str | None = None
    binance_api_secret: str | None = None
    binance_symbols: str | None = None
    total_amount: float | None = None


@router.get("")
def read_settings(db: Session = Depends(get_db), user: User = Depends(require_user)):
    return build_goal(db, user_id=user.id)


@router.put("")
def update_settings(
    payload: SettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    data = payload.model_dump(exclude_unset=True)
    # 总金额写到用户自己；其余仍写全局 settings（目标/币安等）
    if "total_amount" in data and data["total_amount"] is not None:
        user.total_amount = float(data.pop("total_amount") or 0)
    row = get_settings(db)
    if data.get("binance_api_secret") == "":
        data.pop("binance_api_secret")
    for key, value in data.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(user)
    return build_goal(db, user_id=user.id)
