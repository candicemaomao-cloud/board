from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NotifyRecipient
from app.services.notify import (
    CHANNEL_LABEL,
    NotifyError,
    SECRET_FIELDS,
    _text,
    channel_ready,
    enabled_channels,
    send_text,
)


class RecipientError(Exception):
    pass


def _looks_like_placeholder_secret(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return True
    if text in {"******", "••••••", "......", "••••", "****", "123456", "password"}:
        return True
    if set(text) <= {".", "•", "*", " "} and len(text) <= 16:
        return True
    return False


def _valid_telegram_token(value: str) -> bool:
    """BotFather token 形如 123456789:AAH..."""
    text = (value or "").strip()
    if len(text) < 30 or ":" not in text:
        return False
    left, _, right = text.partition(":")
    return left.isdigit() and len(right) >= 20


def _phone(raw) -> str | None:
    phone = _text(raw).replace(" ", "").replace("-", "")
    if phone.startswith("+"):
        phone = phone[1:]
    return phone or None


def to_out(row: NotifyRecipient) -> dict:
    enabled = enabled_channels(row)
    ready = [name for name in enabled if channel_ready(row, name)]
    token = _text(row.telegram_bot_token)
    token_ok = _valid_telegram_token(token) if token else False
    return {
        "id": row.id,
        "name": row.name,
        "notes": row.notes or "",
        "telegram": bool(row.notify_telegram),
        "whatsapp": bool(row.notify_whatsapp),
        "wx": bool(row.notify_wx),
        "telegram_chat_id": _text(row.telegram_chat_id),
        "whatsapp_phone": _text(row.whatsapp_phone),
        "has_telegram_token": bool(token),
        "telegram_token_ok": token_ok,
        "has_whatsapp_apikey": bool(_text(row.whatsapp_apikey)),
        "has_wx_webhook": bool(_text(row.wx_webhook)),
        "has_wx_sendkey": bool(_text(row.wx_sendkey)),
        "channels": [CHANNEL_LABEL.get(name, name) for name in ready],
        "ready": ready,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_recipients(db: Session) -> list[NotifyRecipient]:
    return list(db.scalars(select(NotifyRecipient).order_by(NotifyRecipient.id.desc())))


def get_recipient(db: Session, recipient_id: int) -> NotifyRecipient | None:
    return db.get(NotifyRecipient, recipient_id)


def _apply(row: NotifyRecipient, payload: dict, *, creating: bool) -> None:
    title = _text(payload.get("name"))
    if not title:
        raise RecipientError("请填写名字")
    row.name = title[:64]
    row.notes = _text(payload.get("notes")) or None
    row.notify_telegram = 1 if payload.get("telegram") else 0
    row.notify_whatsapp = 1 if payload.get("whatsapp") else 0
    row.notify_wx = 1 if payload.get("wx") else 0
    if "telegram_chat_id" in payload or creating:
        row.telegram_chat_id = _text(payload.get("telegram_chat_id")) or None
    if "whatsapp_phone" in payload or creating:
        row.whatsapp_phone = _phone(payload.get("whatsapp_phone"))
    for key in SECRET_FIELDS:
        if key not in payload:
            continue
        value = _text(payload.get(key))
        if not value or _looks_like_placeholder_secret(value):
            # 留空 / 占位 / 浏览器自动填充垃圾值 = 不改已保存密钥
            continue
        if key == "telegram_bot_token" and not _valid_telegram_token(value):
            raise RecipientError(
                "Telegram Bot Token 格式不对（应类似 123456789:AAH…，从 @BotFather 复制完整 token）"
            )
        setattr(row, key, value)
    # 勾了 Telegram 但库里已是坏 token：提示重填，不要 silently 当 ready
    if row.notify_telegram:
        tok = _text(row.telegram_bot_token)
        if tok and not _valid_telegram_token(tok):
            raise RecipientError(
                "已保存的 Telegram Token 无效（可能被浏览器自动填充覆盖）。"
                "请重新粘贴 @BotFather 的完整 Token 后再保存"
            )
    missing = [name for name in enabled_channels(row) if not channel_ready(row, name)]
    if missing:
        labels = {
            "telegram": "Telegram token 和 chat id",
            "whatsapp": "WhatsApp 手机号和 apikey",
            "wx": "企业微信 webhook 或 Server酱 SendKey",
        }
        raise RecipientError("已勾选但还没填：" + "、".join(labels[name] for name in missing))


def create_recipient(db: Session, payload: dict) -> NotifyRecipient:
    title = _text(payload.get("name"))
    if not title:
        raise RecipientError("请填写名字")
    if db.scalar(select(NotifyRecipient).where(NotifyRecipient.name == title)):
        raise RecipientError("已经有同名推送人")
    row = NotifyRecipient(name=title[:64])
    _apply(row, payload, creating=True)
    if not enabled_channels(row):
        raise RecipientError("请至少勾选一个渠道：Telegram / WhatsApp / 微信")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_recipient(db: Session, row: NotifyRecipient, payload: dict) -> NotifyRecipient:
    title = _text(payload.get("name"))
    if title:
        other = db.scalar(
            select(NotifyRecipient).where(NotifyRecipient.name == title, NotifyRecipient.id != row.id)
        )
        if other:
            raise RecipientError("已经有同名推送人")
    _apply(row, payload, creating=False)
    if not enabled_channels(row):
        raise RecipientError("请至少勾选一个渠道：Telegram / WhatsApp / 微信")
    db.commit()
    db.refresh(row)
    return row


def delete_recipient(db: Session, row: NotifyRecipient) -> None:
    db.delete(row)
    db.commit()


def test_recipient(db: Session, row: NotifyRecipient) -> dict:
    if not enabled_channels(row):
        raise NotifyError("先勾选至少一个渠道并保存")
    results = send_text(row, f"P&L Board 测试：{row.name}，推送已接通。")
    if not any(item["ok"] for item in results):
        raise NotifyError("；".join(item["error"] for item in results if item["error"]) or "测试发送失败")
    return {"ok": True, "results": results}
