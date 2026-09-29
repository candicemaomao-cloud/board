from fastapi import APIRouter, HTTPException, Query

from app.services.fed_risk import FedRiskError, build_fed_risk
from app.services.macro import build_group, series_by_ids
from app.services.macro_event_study import (
    MacroStudyError,
    list_study_options,
    run_macro_event_study,
)

router = APIRouter()


def _slim(payload: dict, keep_all_points: bool = True) -> dict:
    if keep_all_points:
        return payload
    keep_ids = set(payload.get("default_ids") or [])
    if payload.get("headline_id"):
        keep_ids.add(payload["headline_id"])
    series = []
    for row in payload.get("series") or []:
        if row.get("core") or row.get("featured") or row["id"] in keep_ids:
            series.append(row)
            continue
        item = {k: v for k, v in row.items() if k != "points"}
        item["latest"] = row.get("latest")
        item["points"] = []
        series.append(item)
    out = dict(payload)
    out["series"] = series
    return out


@router.get("/fed-risk")
def fed_risk(
    force: bool = Query(False, description="跳过缓存强制重算"),
    as_of: str | None = Query(None, description="YYYY-MM，默认最新"),
):
    """宏观因素宏观风险指数 v3：五大模块 + JPY Carry + 共振。as_of 按数据所属期切片回测。"""
    try:
        return build_fed_risk(force=force, as_of=as_of)
    except FedRiskError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"宏观风险指数暂时算不出：{exc}") from exc


@router.get("/event-study/options")
def event_study_options():
    return list_study_options()


@router.get("/event-study")
def event_study(
    symbol: str = Query(..., min_length=1, max_length=32, description="股票或指数代码，如 QQQ"),
    indicator: str = Query("cpi", description="cpi / cpi_core / pce / pce_core / ppi / nfp / pmi"),
    bucket: str = Query("hot", description="hot / near_hot / cold / near / all"),
    lookback_years: int = Query(5, ge=1, le=25, description="只统计近 N 年事件"),
    compare_all: bool = Query(True),
):
    """宏观 Surprise 事件后，标的 1/5/20 交易日收益与波动统计。"""
    try:
        return run_macro_event_study(
            symbol=symbol,
            indicator=indicator,
            bucket=bucket,
            compare_all=compare_all,
            lookback_years=lookback_years,
        )
    except MacroStudyError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"事件统计暂时算不出：{exc}") from exc


@router.get("/{group}")
def macro_group(group: str, detail: bool = False):
    if group not in {"cpi", "ppi", "pce", "nfp", "pmi", "treasury"}:
        raise HTTPException(404, "只支持 cpi / ppi / pce / nfp / pmi / treasury / fed-risk / event-study")
    try:
        payload = build_group(group, detail=detail)
    except Exception as exc:
        raise HTTPException(502, f"宏观因素数据暂时拉不到：{exc}") from exc
    if not payload.get("series"):
        raise HTTPException(502, "宏观因素数据暂时拉不到，请稍后重试")
    keep_all = group in {"pmi", "ppi", "pce", "treasury"} or (group == "nfp" and not detail)
    return _slim(payload, keep_all_points=keep_all)


@router.get("/{group}/history")
def macro_history(group: str, ids: str = Query(default="")):
    if group not in {"cpi", "ppi", "pce", "nfp", "pmi", "treasury"}:
        raise HTTPException(404, "只支持 cpi / ppi / pce / nfp / pmi / treasury")
    wanted = [x.strip() for x in ids.split(",") if x.strip()]
    if not wanted:
        return {"series": []}
    try:
        rows = series_by_ids(group, wanted, detail=group == "nfp")
    except Exception as exc:
        raise HTTPException(502, f"宏观因素数据暂时拉不到：{exc}") from exc
    return {"series": rows}
