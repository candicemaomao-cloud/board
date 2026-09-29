from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.permissions import ALL_PERMS, DEFAULT_USER_PERMS
from app.services.auth import (
    authenticate,
    create_access_token,
    create_user,
    require_admin,
    require_user,
    update_user,
    user_to_out,
)

router = APIRouter()


class LoginBody(BaseModel):
    username: str
    password: str


class UserCreateBody(BaseModel):
    username: str
    password: str
    total_amount: float = 0.0
    permissions: list[str] | None = None
    role: str = "user"


class UserUpdateBody(BaseModel):
    password: str | None = None
    total_amount: float | None = None
    permissions: list[str] | None = None
    role: str | None = None
    is_active: bool | None = None


@router.post("/login")
def login(payload: LoginBody, db: Session = Depends(get_db)):
    user = authenticate(db, payload.username, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_access_token(user.id, user.username)
    return {"access_token": token, "token_type": "bearer", "user": user_to_out(user)}


@router.get("/me")
def me(user: User = Depends(require_user)):
    return user_to_out(user)


@router.get("/permission-catalog")
def permission_catalog(user: User = Depends(require_user)):
    return {
        "items": ALL_PERMS,
        "default_user": DEFAULT_USER_PERMS,
    }


@router.get("/users")
def list_users(_admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(User).order_by(User.id.asc()).all()
    return {"items": [user_to_out(r) for r in rows]}


@router.post("/users")
def add_user(payload: UserCreateBody, _admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        row = create_user(
            db,
            username=payload.username,
            password=payload.password,
            total_amount=payload.total_amount,
            permissions=payload.permissions,
            role=payload.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return user_to_out(row)


@router.put("/users/{user_id}")
def edit_user(
    user_id: int,
    payload: UserUpdateBody,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = db.get(User, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    try:
        row = update_user(db, row, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return user_to_out(row)


@router.delete("/users/{user_id}", status_code=204)
def remove_user(user_id: int, _admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.get(User, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    if row.username == "admin":
        raise HTTPException(status_code=400, detail="不能删除 admin")
    db.delete(row)
    db.commit()
