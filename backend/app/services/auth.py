"""用户认证：密码哈希、JWT、种子 admin、权限判断。"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Position, User
from app.permissions import ALL_CODES, DEFAULT_USER_PERMS, normalize_perms

ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 14
_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    if kind != "pbkdf2":
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()
    return hmac.compare_digest(check, digest)


def create_access_token(user_id: int, username: str) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "exp": datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])


def perms_of(user: User) -> list[str]:
    if user.role == "admin":
        return list(ALL_CODES)
    try:
        raw = json.loads(user.permissions or "[]")
    except json.JSONDecodeError:
        raw = []
    return normalize_perms(raw if isinstance(raw, list) else [])


def has_perm(user: User, code: str) -> bool:
    if user.role == "admin":
        return True
    return code in perms_of(user)


def user_to_out(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "total_amount": float(user.total_amount or 0),
        "permissions": perms_of(user),
        "is_active": bool(user.is_active),
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
    }


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.query(User).filter(User.username == username).first()


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = get_user_by_username(db, username.strip())
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def ensure_admin_seed(db: Session) -> User:
    """保证有 admin/123456；并把无归属持仓/交易/快照挂到 admin。"""
    from app.models import DailySnapshot, TradeLog
    from app.services.capital import book_equity

    admin = get_user_by_username(db, "admin")
    if not admin:
        try:
            admin = User(
                username="admin",
                password_hash=hash_password("123456"),
                role="admin",
                total_amount=0.0,
                permissions=json.dumps(ALL_CODES),
                is_active=1,
            )
            db.add(admin)
            db.commit()
            db.refresh(admin)
        except Exception:
            db.rollback()
            admin = get_user_by_username(db, "admin")
            if not admin:
                raise
    # 约定：种子账号密码固定为 123456（用户明确要求）
    admin.password_hash = hash_password("123456")
    admin.role = "admin"
    admin.is_active = 1
    admin.permissions = json.dumps(ALL_CODES)
    db.commit()
    db.refresh(admin)

    for model in (Position, TradeLog, DailySnapshot):
        orphan = db.query(model).filter(model.user_id.is_(None)).all()
        if orphan:
            for row in orphan:
                row.user_id = admin.id
            db.commit()

    # admin 总金额为空时，用当前账面资产回填，方便和历史看板对齐
    if not admin.total_amount:
        try:
            admin.total_amount = float(book_equity(db, user_id=admin.id) or 0)
            db.commit()
            db.refresh(admin)
        except Exception:
            db.rollback()
    return admin


def require_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if not creds or not creds.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    try:
        payload = decode_token(creds.credentials)
        uid = int(payload.get("sub") or 0)
    except (JWTError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效，请重新登录") from exc
    user = db.get(User, uid)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已停用")
    return user


def require_perm(code: str):
    def _dep(user: User = Depends(require_user)) -> User:
        if not has_perm(user, code):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"无权限：{code}")
        return user

    return _dep


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    total_amount: float = 0.0,
    permissions: list[str] | None = None,
    role: str = "user",
) -> User:
    name = (username or "").strip()
    if not name or len(name) < 2:
        raise ValueError("用户名至少 2 个字符")
    if not password or len(password) < 4:
        raise ValueError("密码至少 4 位")
    if get_user_by_username(db, name):
        raise ValueError("用户名已存在")
    if role not in {"admin", "user"}:
        raise ValueError("角色只能是 admin 或 user")
    perms = ALL_CODES if role == "admin" else normalize_perms(permissions or DEFAULT_USER_PERMS)
    row = User(
        username=name,
        password_hash=hash_password(password),
        role=role,
        total_amount=float(total_amount or 0),
        permissions=json.dumps(perms),
        is_active=1,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_user(db: Session, row: User, data: dict) -> User:
    if "password" in data and data["password"]:
        if len(data["password"]) < 4:
            raise ValueError("密码至少 4 位")
        row.password_hash = hash_password(data["password"])
    if "total_amount" in data and data["total_amount"] is not None:
        row.total_amount = float(data["total_amount"])
    if "permissions" in data and data["permissions"] is not None:
        if row.role == "admin":
            row.permissions = json.dumps(ALL_CODES)
        else:
            row.permissions = json.dumps(normalize_perms(data["permissions"]))
    if "is_active" in data and data["is_active"] is not None:
        if row.username == "admin" and not data["is_active"]:
            raise ValueError("不能停用 admin")
        row.is_active = 1 if data["is_active"] else 0
    if "role" in data and data["role"]:
        if row.username == "admin" and data["role"] != "admin":
            raise ValueError("不能取消 admin 角色")
        if data["role"] not in {"admin", "user"}:
            raise ValueError("角色只能是 admin 或 user")
        row.role = data["role"]
        if row.role == "admin":
            row.permissions = json.dumps(ALL_CODES)
    db.commit()
    db.refresh(row)
    return row
