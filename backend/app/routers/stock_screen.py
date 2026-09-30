from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import StockScreenPreset, User
from app.services.auth import has_perm, require_user
from app.services.daily_watch import DailyWatchError, create_watch, list_watches, lookup_quote
from app.services.stock_screen import ScreenTooLarge, options, run

router = APIRouter()


class ScreenBody(BaseModel):
    logic: str = "all"
    filters: dict = Field(default_factory=dict)
    sort: str = "market_cap"
    sort_dir: str = "desc"
    columns: list[str] = Field(default_factory=list)
    universe: str = "sp500"


class PresetBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    config: dict


class BulkAddBody(BaseModel):
    rows: list[dict] = Field(min_length=1, max_length=1000)


def _preset_out(row):
    try:
        config = json.loads(row.config or "{}")
    except json.JSONDecodeError:
        config = {}
    return {"id": row.id, "name": row.name, "config": config,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None}


@router.get("/options")
def screen_options():
    return options()


@router.post("/run")
def run_screen(payload: ScreenBody):
    try:
        return run(payload.model_dump())
    except ScreenTooLarge as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"股票筛选暂不可用：{exc}") from exc


@router.get("/presets")
def presets(db: Session = Depends(get_db), user: User = Depends(require_user)):
    rows = db.scalars(select(StockScreenPreset).where(StockScreenPreset.user_id == user.id).order_by(StockScreenPreset.updated_at.desc()))
    return {"items": [_preset_out(row) for row in rows]}


@router.post("/presets")
def save_preset(payload: PresetBody, db: Session = Depends(get_db), user: User = Depends(require_user)):
    name = payload.name.strip()
    row = db.scalar(select(StockScreenPreset).where(StockScreenPreset.user_id == user.id, StockScreenPreset.name == name))
    if row:
        row.config = json.dumps(payload.config, ensure_ascii=False)
    else:
        row = StockScreenPreset(user_id=user.id, name=name, config=json.dumps(payload.config, ensure_ascii=False))
        db.add(row)
    db.commit(); db.refresh(row)
    return _preset_out(row)


@router.delete("/presets/{preset_id}", status_code=204)
def delete_preset(preset_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = db.get(StockScreenPreset, preset_id)
    if not row or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="筛选方案不存在")
    db.delete(row); db.commit()


@router.post("/bulk-add")
def bulk_add(payload: BulkAddBody, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if not has_perm(user, "btn.daily_watch.write"):
        raise HTTPException(status_code=403, detail="无权限：股票列表增删改")
    existing = {(row.symbol or "").upper() for row in list_watches(db)}
    added, skipped, failed = [], [], []
    for item in payload.rows:
        symbol = str(item.get("symbol") or "").strip().upper()
        if not symbol or symbol in existing:
            skipped.append(symbol)
            continue
        try:
            quote = lookup_quote(symbol)
            create_watch(db, {
                **quote, "gamma_low": None, "gamma_high": None, "regression_line": None,
                "max_pain": None, "short_entry_price": None, "long_entry_price": None,
                "is_potential": False, "market_direction": "横盘",
                "sector": item.get("sector_zh") or "其他", "events": None,
            })
            existing.add(symbol); added.append(symbol)
        except Exception:
            failed.append(symbol)
    return {"added": added, "skipped": skipped, "failed": failed}
