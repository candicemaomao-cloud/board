from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from app.models import Alert, AppSettings, NotifyRecipient
from app.services.goal import get_settings

CHANNELS = ("telegram", "whatsapp", "wx")
CHANNEL_LABEL = {"telegram": "Telegram", "whatsapp": "WhatsApp", "wx": "微信"}
SECRET_FIELDS = ("telegram_bot_token", "whatsapp_apikey", "wx_webhook", "wx_sendkey")
CN = ZoneInfo("Asia/Shanghai")


class NotifyError(Exception):
    pass


def _on(row: AppSettings, name: str) -> bool:
    return bool(getattr(row, f"notify_{name}", 0))


def _text(value) -> str:
    return str(value or "").strip()


def _mask(value: str | None) -> str:
    text = _text(value)
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}…{text[-4:]}"


def enabled_channels(row: AppSettings) -> list[str]:
    return [name for name in CHANNELS if _on(row, name)]


def channel_ready(row: AppSettings, name: str) -> bool:
    if name == "telegram":
        token = _text(row.telegram_bot_token)
        chat = _text(row.telegram_chat_id)
        if not token or not chat:
            return False
        # 被自动填充成 123456 这类垃圾值时不当作就绪
        if ":" not in token or len(token) < 30:
            return False
        return True
    if name == "whatsapp":
        return bool(_text(row.whatsapp_phone) and _text(row.whatsapp_apikey))
    if name == "wx":
        return bool(_text(row.wx_webhook) or _text(row.wx_sendkey))
    return False


def notify_status(db) -> dict:
    row = get_settings(db)
    enabled = enabled_channels(row)
    return {
        "telegram": bool(row.notify_telegram),
        "whatsapp": bool(row.notify_whatsapp),
        "wx": bool(row.notify_wx),
        "telegram_chat_id": _text(row.telegram_chat_id),
        "whatsapp_phone": _text(row.whatsapp_phone),
        "has_telegram_token": bool(_text(row.telegram_bot_token)),
        "has_whatsapp_apikey": bool(_text(row.whatsapp_apikey)),
        "has_wx_webhook": bool(_text(row.wx_webhook)),
        "has_wx_sendkey": bool(_text(row.wx_sendkey)),
        "telegram_bot_token": _mask(row.telegram_bot_token),
        "whatsapp_apikey": _mask(row.whatsapp_apikey),
        "wx_webhook": _mask(row.wx_webhook),
        "wx_sendkey": _mask(row.wx_sendkey),
        "enabled": enabled,
        "ready": [name for name in enabled if channel_ready(row, name)],
    }


def save_notify(db, payload: dict) -> dict:
    row = get_settings(db)
    flags = {
        "notify_telegram": 1 if payload.get("telegram") else 0,
        "notify_whatsapp": 1 if payload.get("whatsapp") else 0,
        "notify_wx": 1 if payload.get("wx") else 0,
    }
    for key, value in flags.items():
        setattr(row, key, value)
    if "telegram_chat_id" in payload:
        row.telegram_chat_id = _text(payload.get("telegram_chat_id")) or None
    if "whatsapp_phone" in payload:
        phone = _text(payload.get("whatsapp_phone")).replace(" ", "").replace("-", "")
        if phone.startswith("+"):
            phone = phone[1:]
        row.whatsapp_phone = phone or None
    for key in SECRET_FIELDS:
        if key not in payload:
            continue
        value = _text(payload.get(key))
        if not value:
            continue
        setattr(row, key, value)
    missing = [name for name in enabled_channels(row) if not channel_ready(row, name)]
    if missing:
        labels = {"telegram": "Telegram token 和 chat id", "whatsapp": "WhatsApp 手机号和 apikey", "wx": "企业微信 webhook 或 Server酱 SendKey"}
        raise NotifyError("已勾选但还没填：" + "、".join(labels[name] for name in missing))
    db.commit()
    db.refresh(row)
    return notify_status(db)


def format_hit(row: Alert, result: dict | None = None, *, test: bool = False) -> str:
    detail = result if isinstance(result, dict) else {}
    price = detail.get("price")
    if price is None:
        price = row.last_price
    if isinstance(price, (int, float)):
        price_text = f"{price:.2f}" if abs(float(price)) >= 1 else f"{price:.4f}"
    else:
        price_text = "—"
    strategy = (detail.get("strategy_label") or detail.get("strategy_name") or "").strip()
    if not strategy:
        clauses = detail.get("clauses") or []
        hits = [item.get("name") for item in clauses if item.get("hit") and item.get("name")]
        strategy = "、".join(hits)
    asof = detail.get("asof") or row.last_asof or "—"
    sent = datetime.now(CN).strftime("%Y-%m-%d %H:%M")
    lines = []
    if test:
        lines.append("首次推送仅为测试")
    else:
        lines.append("策略命中")
    lines.extend(
        [
            f"代码：{row.symbol}",
            f"价格：{price_text}",
            f"策略：{strategy or '—'}",
            f"K线：{asof} 美东",
            f"推送：{sent} 北京",
        ]
    )
    if row.notes:
        lines.append(f"备注：{row.notes}")
    return "\n".join(lines)


def _telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    with httpx.Client(timeout=15.0) as client:
        res = client.post(url, json={"chat_id": chat_id, "text": text})
        data = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
        if res.status_code >= 400 or not data.get("ok", True):
            desc = str(data.get("description") or res.text or "Telegram 发送失败")
            if "not found" in desc.lower() or res.status_code == 404:
                raise NotifyError(
                    "Telegram 返回 Not Found：Bot Token 无效或已被覆盖，请重新粘贴正确的 Token 再保存"
                )
            raise NotifyError(desc[:180])


def _whatsapp(phone: str, apikey: str, text: str) -> None:
    url = "https://api.callmebot.com/whatsapp.php"
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        res = client.get(url, params={"phone": phone, "text": text, "apikey": apikey})
        body = (res.text or "").strip()
        if res.status_code >= 400 or "error" in body.lower()[:80]:
            raise NotifyError((body or "WhatsApp 发送失败")[:180])


def _wx_webhook(webhook: str, text: str) -> None:
    with httpx.Client(timeout=15.0) as client:
        res = client.post(webhook, json={"msgtype": "text", "text": {"content": text}})
        data = {}
        try:
            data = res.json()
        except Exception:
            data = {}
        if res.status_code >= 400 or data.get("errcode") not in (None, 0):
            raise NotifyError(str(data.get("errmsg") or res.text or "企业微信发送失败")[:180])


def _wx_serverchan(sendkey: str, text: str) -> None:
    title, _, body = text.partition("\n")
    url = f"https://sctapi.ftqq.com/{quote(sendkey, safe='')}.send"
    with httpx.Client(timeout=15.0) as client:
        res = client.post(url, data={"title": title[:32] or "警报命中", "desp": body or text})
        data = {}
        try:
            data = res.json()
        except Exception:
            data = {}
        if res.status_code >= 400 or data.get("code") not in (None, 0):
            raise NotifyError(str(data.get("message") or res.text or "Server酱发送失败")[:180])


def send_text(row, text: str, *, channels: list[str] | None = None) -> list[dict]:
    targets = channels or enabled_channels(row)
    results = []
    for name in targets:
        if not channel_ready(row, name):
            results.append({"channel": name, "ok": False, "error": "还没填完整"})
            continue
        try:
            if name == "telegram":
                _telegram(_text(row.telegram_bot_token), _text(row.telegram_chat_id), text)
            elif name == "whatsapp":
                _whatsapp(_text(row.whatsapp_phone), _text(row.whatsapp_apikey), text)
            elif name == "wx":
                errors = []
                sent = False
                if _text(row.wx_webhook):
                    try:
                        _wx_webhook(_text(row.wx_webhook), text)
                        sent = True
                    except NotifyError as exc:
                        errors.append(str(exc))
                if _text(row.wx_sendkey):
                    try:
                        _wx_serverchan(_text(row.wx_sendkey), text)
                        sent = True
                    except NotifyError as exc:
                        errors.append(str(exc))
                if not sent:
                    raise NotifyError("；".join(errors) or "微信发送失败")
            results.append({"channel": name, "ok": True, "error": None})
        except (NotifyError, httpx.HTTPError) as exc:
            results.append({"channel": name, "ok": False, "error": str(exc)[:180]})
    return results


def list_ready_targets(db) -> list:
    rows = list(db.scalars(select(NotifyRecipient).order_by(NotifyRecipient.id.asc())))
    ready = [
        row
        for row in rows
        if any(channel_ready(row, name) for name in enabled_channels(row))
    ]
    if ready:
        return ready
    settings = get_settings(db)
    if any(channel_ready(settings, name) for name in enabled_channels(settings)):
        return [settings]
    return []


def send_all(db, text: str, *, recipient_ids: list[int] | None = None) -> list[dict]:
    if recipient_ids:
        rows = []
        for rid in recipient_ids:
            row = db.get(NotifyRecipient, int(rid))
            if row and any(channel_ready(row, name) for name in enabled_channels(row)):
                rows.append(row)
        targets = rows
    else:
        targets = list_ready_targets(db)
    results = []
    for row in targets:
        part = send_text(row, text)
        who = getattr(row, "name", None) or "默认"
        for item in part:
            item["who"] = who
        results.extend(part)
    return results


def send_test(db) -> dict:
    targets = list_ready_targets(db)
    if not targets:
        raise NotifyError("先在「推送人」里添加并保存至少一个渠道")
    results = send_all(db, "P&L Board 测试：推送已接通。")
    if not any(item["ok"] for item in results):
        raise NotifyError("；".join(item["error"] for item in results if item["error"]) or "测试发送失败")
    return {"ok": True, "results": results}


def _pack_results(results: list[dict]) -> dict:
    ok = [CHANNEL_LABEL.get(item["channel"], item["channel"]) for item in results if item.get("ok")]
    bad = [
        f"{CHANNEL_LABEL.get(item['channel'], item['channel'])}：{item.get('error')}"
        for item in results
        if not item.get("ok") and item.get("error")
    ]
    return {
        "ok": bool(ok),
        "channels": ok,
        "error": "；".join(bad) or None,
        "results": results,
    }


def probe_push(db, label: str) -> dict:
    if not list_ready_targets(db):
        return {"ok": False, "channels": [], "error": "还没添加推送人，先去「推送人」里配 WhatsApp / Telegram / 微信", "results": []}
    text = f"推送测试 {label}\n已允许推送到手机。收到这条说明通道正常。"
    return _pack_results(send_all(db, text))


def send_test_hit(db, alert: Alert, result: dict | None = None) -> dict:
    try:
        raw = json.loads(getattr(alert, "recipient_ids", None) or "[]")
    except Exception:
        raw = []
    rids = [int(x) for x in raw if str(x).isdigit() or isinstance(x, int)]
    if not rids:
        return {"ok": False, "channels": [], "error": "请先选推送人", "results": []}
    results = send_all(db, format_hit(alert, result, test=True), recipient_ids=rids)
    packed = _pack_results(results)
    if not packed["ok"] and not packed["error"]:
        packed["error"] = "测试发送失败"
    return packed


def maybe_notify(db, alert: Alert, result: dict | None = None) -> list[dict]:
    if not alert.last_hit or alert.last_error or not alert.last_asof:
        return []
    if not alert.allow_push:
        return []
    if alert.last_notified_asof == alert.last_asof:
        return []
    try:
        raw = json.loads(getattr(alert, "recipient_ids", None) or "[]")
    except Exception:
        raw = []
    rids = [int(x) for x in raw if str(x).isdigit() or isinstance(x, int)]
    if not rids:
        return []
    results = send_all(db, format_hit(alert, result), recipient_ids=rids)
    if any(item["ok"] for item in results):
        alert.last_notified_asof = alert.last_asof
    return results
