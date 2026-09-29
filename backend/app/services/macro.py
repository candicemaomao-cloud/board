from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from time import sleep, time
from urllib.parse import urlencode

import httpx

HEADERS = {"User-Agent": "PnlBoard/1.0 (personal trading dashboard)"}
NOMIC = "https://api.db.nomics.world/v22"
CACHE_DIR = Path("data/macro")
CACHE_TTL = 6 * 3600
CACHE_VER = 3
START = "1960-01"
TIMEOUT = 75.0
BLS_V2 = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BLS_V1 = "https://api.bls.gov/publicAPI/v1/timeseries/data/"

CPI_ZH = {
    "SA0": "总 CPI",
    "SA0L1E": "核心 CPI（除食品能源）",
    "SA0LE": "除能源",
    "SA0L1": "除食品",
    "SAF": "食品饮料",
    "SAF1": "食品",
    "SAF11": "在家吃饭",
    "SEFV": "外出就餐",
    "SA0E": "能源",
    "SACE": "能源商品",
    "SEHF": "能源服务",
    "SEHF01": "电力",
    "SEHF02": "管道天然气",
    "SETB": "机动车燃料",
    "SETB01": "汽油",
    "SAH": "住房",
    "SAH1": "Shelter",
    "SEHA": "房租",
    "SEHC": "业主等值租金",
    "SAH2": "燃料与公用事业",
    "SAH3": "家居用品",
    "SAA": "服装",
    "SAT": "交通",
    "SAT1": "私人交通",
    "SETA": "新车与二手车",
    "SETA01": "新车",
    "SETA02": "二手车",
    "SETG": "公共交通",
    "SETG01": "机票",
    "SAM": "医疗",
    "SAM1": "医疗商品",
    "SAM2": "医疗服务",
    "SAR": "娱乐",
    "SAE": "教育通信",
    "SAE1": "教育",
    "SAE2": "通信",
    "SAG": "其他商品和服务",
    "SAC": "商品",
    "SACL1E": "核心商品",
    "SAS": "服务",
    "SASLE": "核心服务",
}

CPI_DEFAULT = ["SA0", "SA0L1E"]
CPI_FEATURED = [
    "SA0", "SA0L1E", "SAF1", "SA0E", "SAH", "SAH1", "SEHA", "SEHC",
    "SAA", "SAT", "SETA01", "SETA02", "SETB01", "SAM", "SAR", "SAE",
    "SAG", "SAC", "SAS", "SAF11", "SEFV", "SEHF01", "SASLE",
]

# BLS PPI Final Demand, seasonally adjusted. Names follow the official release, not DBnomics metadata.
PPI_CATALOG = [
    ("WPSFD4", "总 PPI（最终需求）", True),
    ("WPSFD49104", "核心 PPI（除食品能源）", True),
    ("WPSFD49116", "核心 PPI（除食品能源贸易）", True),
    ("WPSFD411", "最终需求商品", True),
    ("WPSFD42", "最终需求服务", True),
    ("WPSFD412", "食品", False),
    ("WPSFD413", "能源", False),
    ("WPSFD43", "建筑", False),
]
PPI_DEFAULT = ["WPSFD4", "WPSFD49104", "WPSFD49116"]

# BEA NIPA Table 2.8.4 monthly PCE price indexes. Fed watches headline + core.
PCE_CATALOG = [
    ("DPCERG-M", "总 PCE", True),
    ("DPCCRG-M", "核心 PCE（除食品能源）", True),
    ("IA001176-M", "核心 PCE（除食品能源住房）", True),
    ("IA001260-M", "服务业（除能源住房）", True),
    ("DGDSRG-M", "商品", False),
    ("DSERRG-M", "服务", False),
    ("DDURRG-M", "耐用品", False),
    ("DNDGRG-M", "非耐用品", False),
    ("DFXARG-M", "食品饮料", False),
    ("DNRGRG-M", "能源", False),
    ("DHUTRG-M", "住房与公用事业", False),
    ("DHLCRG-M", "医疗", False),
    ("DPCMRG-M", "市场法 PCE", False),
    ("DPCXRG-M", "市场法核心 PCE", False),
]
PCE_DEFAULT = ["DPCERG-M", "DPCCRG-M"]
PCE_DATASET = "NIPA-T20804"

# Fed H.15 Treasury constant maturity yields (business day), DBnomics FED/H15
TREASURY_CATALOG = [
    ("RIFLGFCY02_N.B", "2年期美债收益率", True),
    ("RIFLGFCY05_N.B", "5年期美债收益率", True),
    ("RIFLGFCY10_N.B", "10年期美债收益率", True),
    ("RIFLGFCY30_N.B", "30年期美债收益率", True),
]
TREASURY_DEFAULT = ["RIFLGFCY02_N.B", "RIFLGFCY10_N.B", "RIFLGFCY30_N.B"]

NFP_SUPER_ZH = {
    "00": "非农总计",
    "05": "私营",
    "06": "商品生产",
    "07": "服务业",
    "08": "私营服务业",
    "10": "采矿与伐木",
    "20": "建筑",
    "30": "制造业",
    "31": "耐用品制造",
    "32": "非耐用品制造",
    "40": "贸易、运输与公用事业",
    "41": "批发",
    "42": "零售",
    "43": "运输仓储",
    "44": "公用事业",
    "50": "信息",
    "55": "金融活动",
    "60": "专业与商务服务",
    "65": "教育与医疗",
    "70": "休闲酒店",
    "80": "其他服务",
    "90": "政府",
}

NFP_HEADLINE = "CES0000000001"
NFP_PRIVATE = "CES0500000001"
NFP_GOVT = "CES9000000001"
NFP_DEFAULT_IDS = [NFP_HEADLINE, NFP_PRIVATE, NFP_GOVT]
NFP_CORE_INDUSTRIES = [
    "00000000", "05000000", "06000000", "08000000",
    "10000000", "20000000", "30000000", "31000000", "32000000",
    "40000000", "41420000", "42000000", "43000000", "44220000",
    "50000000", "55000000", "55520000", "55530000",
    "60000000", "60540000", "60550000", "60560000", "60561320",
    "65000000", "65610000", "65620000",
    "70000000", "70710000", "70720000",
    "80000000", "90000000", "90910000", "90920000", "90930000",
]

PMI_MAN_PARTS = [
    ("neword", "新订单"),
    ("production", "生产"),
    ("employment", "就业"),
    ("supdel", "供应商交付"),
    ("inventories", "库存"),
]
PMI_SVC_PARTS = [
    ("nm-neword", "新订单"),
    ("nm-busact", "商业活动"),
    ("nm-employment", "就业"),
    ("nm-supdel", "供应商交付"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{name}.json"


def _read_cache(name: str):
    path = _cache_path(name)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("ver") != CACHE_VER:
        return None
    if time() - payload.get("ts", 0) > CACHE_TTL:
        return None
    return payload.get("data")


def _write_cache(name: str, data):
    path = _cache_path(name)
    path.write_text(json.dumps({"ts": time(), "ver": CACHE_VER, "data": data}, ensure_ascii=False))


def _get(client: httpx.Client, url: str) -> dict:
    last = None
    for attempt in range(3):
        try:
            res = client.get(url)
            res.raise_for_status()
            return res.json()
        except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as exc:
            last = exc
            if attempt < 2:
                sleep(1.2 * (attempt + 1))
    raise last


def _nomics_url(provider: str, dataset: str, series: str | None = None, **params) -> str:
    base = f"{NOMIC}/series/{provider}/{dataset}"
    if series:
        base += f"/{series}"
    params.setdefault("metadata", "false")
    if "dimensions" in params and not isinstance(params["dimensions"], str):
        params["dimensions"] = json.dumps(params["dimensions"], separators=(",", ":"))
    query = urlencode(params)
    return f"{base}?{query}" if query else base


def _docs(payload: dict) -> list[dict]:
    return list(((payload.get("series") or {}).get("docs")) or [])


def _num(value) -> float | None:
    if value is None or value == "NA":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _points(doc: dict, start: str = START) -> list[dict]:
    periods = doc.get("period") or []
    values = doc.get("value") or []
    out = []
    for period, raw in zip(periods, values):
        if not period or period < start:
            continue
        value = _num(raw)
        if value is None:
            continue
        out.append({"date": period, "value": round(value, 4)})
    return out


def _with_changes(points: list[dict], pct: bool = True) -> list[dict]:
    indexed = {row["date"]: row["value"] for row in points}
    out = []
    for i, row in enumerate(points):
        prev = points[i - 1]["value"] if i else None
        year = indexed.get(f"{int(row['date'][:4]) - 1}{row['date'][4:]}")
        mom = None
        yoy = None
        if prev not in (None, 0):
            mom = (row["value"] - prev) / prev * 100 if pct else row["value"] - prev
        if year not in (None, 0):
            yoy = (row["value"] - year) / year * 100 if pct else row["value"] - year
        item = dict(row)
        item["mom"] = None if mom is None else round(mom, 3)
        item["yoy"] = None if yoy is None else round(yoy, 3)
        out.append(item)
    return out


def _latest(points: list[dict]) -> dict | None:
    return points[-1] if points else None


def _pack(series_id: str, name: str, points: list[dict], extra: dict | None = None, pct: bool = True) -> dict:
    rows = _with_changes(points, pct=pct)
    last = _latest(rows)
    body = {
        "id": series_id,
        "name": name,
        "points": rows,
        "latest": last,
        "start": rows[0]["date"] if rows else None,
        "end": last["date"] if last else None,
    }
    if extra:
        body.update(extra)
    return body


def _client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, headers=HEADERS, follow_redirects=True)


def _bls_parse_series(block: dict) -> list[dict]:
    out = []
    for row in block.get("data") or []:
        period = row.get("period") or ""
        if not period.startswith("M") or period == "M13":
            continue
        value = _num(row.get("value"))
        year = row.get("year")
        if value is None or not year:
            continue
        out.append({"date": f"{year}-{period[1:]}", "value": round(value, 4)})
    out.sort(key=lambda item: item["date"])
    return out


def _bls_post(ids: list[str], start_year: int, end_year: int) -> dict[str, list[dict]]:
    body = {"seriesid": ids, "startyear": str(start_year), "endyear": str(end_year)}
    headers = {**HEADERS, "Content-Type": "application/json"}
    last = None
    for url in (BLS_V2, BLS_V1):
        try:
            with _client() as client:
                res = client.post(url, json=body, headers=headers)
                res.raise_for_status()
                payload = res.json()
        except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError, ValueError) as exc:
            last = exc
            continue
        if payload.get("status") != "REQUEST_SUCCEEDED":
            last = RuntimeError(payload.get("message") or payload.get("status"))
            continue
        out = {}
        for block in ((payload.get("Results") or {}).get("series")) or []:
            sid = block.get("seriesID")
            if sid:
                out[sid] = _bls_parse_series(block)
        if out:
            return out
    if last:
        raise last
    return {}


def _bls_get_one(series_id: str, start_year: int, end_year: int) -> list[dict]:
    url = f"{BLS_V2}{series_id}?startyear={start_year}&endyear={end_year}"
    try:
        with _client() as client:
            payload = _get(client, url)
    except Exception:
        return []
    blocks = ((payload.get("Results") or {}).get("series")) or []
    return _bls_parse_series(blocks[0]) if blocks else []


def _bls_series_map(ids: list[str]) -> dict[str, list[dict]]:
    ids = [sid for sid in ids if sid]
    if not ids:
        return {}
    end_year = datetime.now(timezone.utc).year
    start_year = end_year - 9
    out = {}
    for i in range(0, len(ids), 25):
        chunk = ids[i:i + 25]
        try:
            out.update(_bls_post(chunk, start_year, end_year))
        except Exception:
            for sid in chunk:
                pts = _bls_get_one(sid, max(start_year, end_year - 2), end_year)
                if pts:
                    out[sid] = pts
    return out


def _merge_points(base: list[dict], overlay: list[dict]) -> list[dict]:
    by = {row["date"]: row["value"] for row in base}
    by.update({row["date"]: row["value"] for row in overlay})
    return [{"date": date, "value": by[date]} for date in sorted(by)]


def _meta_of(row: dict) -> dict:
    skip = {"id", "name", "points", "latest", "start", "end"}
    return {k: v for k, v in row.items() if k not in skip}


def _refresh_with_bls(series: list[dict], pct: bool) -> list[dict]:
    ids = [row["id"] for row in series]
    overlay = _bls_series_map(ids)
    if not overlay:
        return series
    out = []
    for row in series:
        extra_pts = overlay.get(row["id"]) or []
        if not extra_pts:
            out.append(row)
            continue
        base = [{"date": p["date"], "value": p["value"]} for p in (row.get("points") or [])]
        out.append(_pack(row["id"], row["name"], _merge_points(base, extra_pts), extra=_meta_of(row), pct=pct))
    return out


def _list_series(client: httpx.Client, provider: str, dataset: str, dimensions: dict, page: int = 500) -> list[dict]:
    rows = []
    offset = 0
    while True:
        payload = _get(client, _nomics_url(
            provider, dataset,
            observations=0, limit=page, offset=offset, dimensions=dimensions, metadata="true",
        ))
        chunk = _docs(payload)
        rows.extend(chunk)
        found = ((payload.get("series") or {}).get("num_found")) or 0
        offset += len(chunk)
        if not chunk or offset >= found:
            break
    return rows


def _fetch_one(client: httpx.Client, provider: str, dataset: str, series_id: str) -> dict | None:
    payload = _get(client, _nomics_url(provider, dataset, series_id, observations=1))
    docs = _docs(payload)
    return docs[0] if docs else None


def _fetch_one_safe(provider: str, dataset: str, series_id: str) -> tuple[str, dict | None]:
    try:
        with _client() as client:
            return series_id, _fetch_one(client, provider, dataset, series_id)
    except Exception:
        return series_id, None


def _fetch_ids(provider: str, dataset: str, ids: list[str]) -> dict[str, dict]:
    out = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(_fetch_one_safe, provider, dataset, sid) for sid in ids]
        for fut in as_completed(futs):
            sid, doc = fut.result()
            if doc:
                out[sid] = doc
    return out


def _fetch_many(client: httpx.Client, provider: str, dataset: str, dimensions: dict, page: int = 20) -> list[dict]:
    rows = []
    offset = 0
    while True:
        payload = _get(client, _nomics_url(
            provider, dataset,
            observations=1, limit=page, offset=offset, dimensions=dimensions,
        ))
        chunk = _docs(payload)
        rows.extend(chunk)
        found = ((payload.get("series") or {}).get("num_found")) or 0
        offset += len(chunk)
        if not chunk or offset >= found:
            break
    return rows


def _cpi_name(item: str, fallback: str = "") -> str:
    return CPI_ZH.get(item) or fallback or item


def build_cpi() -> dict:
    hit = _read_cache("cpi")
    if hit:
        return hit
    dimensions = {"area": ["0000"], "seasonal": ["S"], "periodicity": ["R"], "base": ["S"]}
    series = []
    seen = set()
    with _client() as client:
        docs = _fetch_ids("BLS", "cu", [f"CUSR0000{item}" for item in CPI_FEATURED])
        for item in CPI_FEATURED:
            code = f"CUSR0000{item}"
            doc = docs.get(code)
            pts = _points(doc) if doc else []
            packed = _pack(code, _cpi_name(item, (doc or {}).get("series_name") or item), pts, extra={"item": item, "featured": True})
            series.append(packed)
            seen.add(code)
        try:
            listed = _list_series(client, "BLS", "cu", dimensions)
        except Exception:
            listed = []
        for doc in listed:
            code = doc.get("series_code")
            item = (doc.get("dimensions") or {}).get("item") or ""
            if not code or code in seen:
                continue
            series.append({
                "id": code,
                "name": _cpi_name(item, doc.get("series_name") or item),
                "item": item,
                "featured": False,
                "points": [],
                "latest": None,
                "start": None,
                "end": None,
            })
            seen.add(code)
    series = _refresh_with_bls([row for row in series if row.get("featured")], pct=True) + [row for row in series if not row.get("featured")]
    series.sort(key=lambda row: (0 if row.get("item") in CPI_DEFAULT else 1 if row.get("featured") else 2, row["name"]))
    data = {
        "group": "cpi",
        "title": "CPI",
        "unit": "指数 / 同比%",
        "source": "BLS 劳工部官方最新一期 · 1960 年起长历史来自 DBnomics",
        "updated_at": _now(),
        "default_ids": [row["id"] for row in series if row.get("item") in CPI_DEFAULT],
        "series": series,
    }
    _write_cache("cpi", data)
    return data


def _build_named_index(
    cache_key: str,
    group: str,
    title: str,
    unit: str,
    source: str,
    provider: str,
    dataset: str,
    catalog: list[tuple[str, str, bool]],
    default_ids: list[str],
    bls: bool = False,
) -> dict:
    hit = _read_cache(cache_key)
    if hit:
        names = {sid: name for sid, name, _featured in catalog}
        for row in hit.get("series") or []:
            if row.get("id") in names:
                row["name"] = names[row["id"]]
        hit["default_ids"] = [sid for sid in default_ids if any(row["id"] == sid for row in (hit.get("series") or []))]
        return hit
    ids = [sid for sid, _name, _featured in catalog]
    docs = _fetch_ids(provider, dataset, ids)
    series = []
    for sid, name, featured in catalog:
        doc = docs.get(sid)
        pts = _points(doc) if doc else []
        series.append(_pack(sid, name, pts, extra={"featured": featured, "item": sid}))
    if bls:
        series = _refresh_with_bls(series, pct=True)
    rank = {sid: i for i, sid in enumerate(default_ids)}
    series.sort(key=lambda row: (rank.get(row["id"], 99), 0 if row.get("featured") else 1, row["name"]))
    data = {
        "group": group,
        "title": title,
        "unit": unit,
        "source": source,
        "updated_at": _now(),
        "default_ids": [sid for sid in default_ids if any(row["id"] == sid for row in series)],
        "series": series,
    }
    _write_cache(cache_key, data)
    return data


def build_ppi() -> dict:
    return _build_named_index(
        "ppi", "ppi", "PPI", "指数 / 同比%",
        "BLS 劳工部 PPI 最终需求 · 官方最新一期，长历史来自 DBnomics",
        "BLS", "wp", PPI_CATALOG, PPI_DEFAULT, bls=True,
    )


def build_pce() -> dict:
    return _build_named_index(
        "pce", "pce", "PCE", "指数 / 同比%",
        "BEA 经济分析局 Table 2.8.4 月度物价指数 · 联储看的通胀口径",
        "BEA", PCE_DATASET, PCE_CATALOG, PCE_DEFAULT, bls=False,
    )


def build_treasury() -> dict:
    """美国国债名义常值到期收益率：2年 / 5年 / 10年 / 30年（日频）。"""
    hit = _read_cache("treasury")
    if hit:
        return hit
    docs = _fetch_ids("FED", "H15", [sid for sid, _n, _f in TREASURY_CATALOG])
    series = []
    for sid, name, featured in TREASURY_CATALOG:
        doc = docs.get(sid)
        pts = _points(doc, start="1990-01-01") if doc else []
        # 收益率用绝对变化（bp 级 mom），不做 % 环比
        series.append(
            _pack(sid, name, pts, extra={"featured": featured, "item": sid, "kind": "yield"}, pct=False)
        )
    series.sort(key=lambda row: TREASURY_DEFAULT.index(row["id"]) if row["id"] in TREASURY_DEFAULT else 99)
    data = {
        "group": "treasury",
        "title": "美债收益率",
        "unit": "%（名义常值到期）",
        "source": "宏观因素 H.15 · DBnomics FED/H15（Business day）",
        "updated_at": _now(),
        "default_ids": [sid for sid in TREASURY_DEFAULT if any(row["id"] == sid for row in series)],
        "series": series,
    }
    _write_cache("treasury", data)
    return data


def _nfp_name(industry: str, supersector: str, fallback: str) -> str:
    if industry in ("00000000", "05000000", "06000000", "07000000", "08000000", "90000000"):
        labels = {
            "00000000": "非农总计",
            "05000000": "私营",
            "06000000": "商品生产",
            "07000000": "服务业",
            "08000000": "私营服务业",
            "90000000": "政府",
        }
        return labels[industry]
    return fallback or industry


def _nfp_level(industry: str) -> int:
    text = industry.rstrip("0")
    return max(0, (len(industry) - len(text)) // 2)


def _nfp_from_doc(doc: dict) -> dict | None:
    dims = doc.get("dimensions") or {}
    industry = dims.get("industry") or ""
    supersector = dims.get("supersector") or ""
    code = doc.get("series_code")
    if not code:
        return None
    parts = (doc.get("series_name") or "").replace("-", "–").split("–")
    eng = parts[1].strip() if len(parts) >= 2 else industry
    return _pack(
        code, _nfp_name(industry, supersector, eng), _points(doc), pct=False,
        extra={
            "industry": industry,
            "supersector": supersector,
            "supersector_name": NFP_SUPER_ZH.get(supersector, supersector),
            "level": _nfp_level(industry),
            "core": industry in NFP_CORE_INDUSTRIES,
        },
    )


def _nfp_payload(series: list[dict], detail: bool) -> dict:
    series.sort(key=lambda row: (row.get("supersector") or "", row.get("level") or 0, row["name"]))
    headline = next((row for row in series if row["id"] == NFP_HEADLINE), series[0] if series else None)
    return {
        "group": "nfp",
        "title": "非农就业",
        "unit": "千人",
        "source": "BLS 劳工部官方最新一期 · 1960 年起长历史来自 DBnomics",
        "updated_at": _now(),
        "headline_id": NFP_HEADLINE,
        "default_ids": [sid for sid in NFP_DEFAULT_IDS if any(row["id"] == sid for row in series)] or [NFP_HEADLINE],
        "series": series,
        "headline": headline,
        "detail": detail,
    }


def build_nfp(detail: bool = False) -> dict:
    cache_key = "nfp_all" if detail else "nfp_core"
    hit = _read_cache(cache_key)
    if hit:
        names = {NFP_HEADLINE: "非农总计", NFP_PRIVATE: "私营", NFP_GOVT: "政府"}
        for row in hit.get("series") or []:
            if row.get("id") in names:
                row["name"] = names[row["id"]]
        hit["default_ids"] = [
            sid for sid in NFP_DEFAULT_IDS
            if any(row["id"] == sid for row in (hit.get("series") or []))
        ] or [NFP_HEADLINE]
        return hit

    if not detail:
        series = []
        docs = _fetch_ids("BLS", "ce", [f"CES{industry}01" for industry in NFP_CORE_INDUSTRIES])
        for industry in NFP_CORE_INDUSTRIES:
            code = f"CES{industry}01"
            packed = _nfp_from_doc(docs[code]) if code in docs else None
            if packed is None:
                supersector = industry[:2]
                packed = _pack(
                    code, _nfp_name(industry, supersector, industry), [], pct=False,
                    extra={
                        "industry": industry,
                        "supersector": supersector,
                        "supersector_name": NFP_SUPER_ZH.get(supersector, supersector),
                        "level": _nfp_level(industry),
                        "core": True,
                    },
                )
            series.append(packed)
        series = _refresh_with_bls(series, pct=False)
        data = _nfp_payload(series, False)
        _write_cache("nfp_core", data)
        return data

    core = {row["id"]: row for row in build_nfp(False).get("series") or []}
    series = []
    with _client() as client:
        listed = _list_series(client, "BLS", "ce", {"data_type": ["01"], "seasonal": ["S"]})
    for doc in listed:
        packed = _nfp_from_doc(doc)
        if packed:
            series.append(core.get(packed["id"]) or packed)
    data = _nfp_payload(series, True)
    _write_cache("nfp_all", data)
    return data


def series_by_ids(group: str, ids: list[str], detail: bool = False) -> list[dict]:
    data = build_group(group, detail=False)
    by_id = {row["id"]: row for row in data.get("series") or []}
    if group == "nfp":
        all_rows = _read_cache("nfp_all")
        if all_rows:
            by_id.update({row["id"]: row for row in all_rows.get("series") or []})
    out = []
    with _client() as client:
        for sid in ids:
            row = by_id.get(sid)
            if row and row.get("points"):
                out.append(row)
                continue
            if group == "pmi":
                continue
            if group == "treasury":
                provider, dataset = "FED", "H15"
            elif group == "pce":
                provider, dataset = "BEA", PCE_DATASET
            elif group == "ppi":
                provider, dataset = "BLS", "wp"
            elif group == "cpi":
                provider, dataset = "BLS", "cu"
            else:
                provider, dataset = "BLS", "ce"
            try:
                doc = _fetch_one(client, provider, dataset, sid)
            except Exception:
                continue
            if not doc:
                continue
            if group == "nfp":
                packed = _nfp_from_doc(doc)
            elif group == "treasury":
                name = next((n for i, n, _f in TREASURY_CATALOG if i == sid), doc.get("series_name") or sid)
                packed = _pack(sid, name, _points(doc, start="1990-01-01"), extra={"featured": sid in TREASURY_DEFAULT, "item": sid, "kind": "yield"}, pct=False)
            elif group == "ppi":
                name = next((n for i, n, _f in PPI_CATALOG if i == sid), doc.get("series_name") or sid)
                packed = _pack(sid, name, _points(doc), extra={"featured": sid in PPI_DEFAULT, "item": sid})
            elif group == "pce":
                name = next((n for i, n, _f in PCE_CATALOG if i == sid), doc.get("series_name") or sid)
                packed = _pack(sid, name, _points(doc), extra={"featured": sid in PCE_DEFAULT, "item": sid})
            else:
                item = (doc.get("dimensions") or {}).get("item") or ""
                packed = _pack(
                    sid, _cpi_name(item, doc.get("series_name") or sid), _points(doc),
                    extra={"item": item, "featured": item in CPI_FEATURED},
                )
            if packed:
                out.append(packed)
    have = {row["id"] for row in out}
    for sid in ids:
        if sid not in have:
            out.append(by_id.get(sid) or {"id": sid, "name": sid, "points": []})
    if group != "pmi" and group != "pce":
        out = _refresh_with_bls(out, pct=group in {"cpi", "ppi"})
    return out


def _pmi_doc(client: httpx.Client, dataset: str, code: str) -> dict | None:
    try:
        return _fetch_one(client, "ISM", dataset, code)
    except httpx.HTTPError:
        return None


def _pmi_points(doc: dict | None, lo: float = 20, hi: float = 90) -> list[dict]:
    if not doc:
        return []
    rows = []
    for row in _points(doc, start="1948-01"):
        if lo <= row["value"] <= hi:
            rows.append(row)
    return rows


def _average_points(groups: list[list[dict]]) -> list[dict]:
    dates = sorted({row["date"] for group in groups for row in group})
    maps = [{row["date"]: row["value"] for row in group} for group in groups]
    out = []
    for date in dates:
        vals = [mp[date] for mp in maps if date in mp]
        if len(vals) < max(1, len(groups) - 1):
            continue
        out.append({"date": date, "value": round(sum(vals) / len(vals), 2)})
    return out


def _merge_prefer(primary: list[dict], fallback: list[dict]) -> list[dict]:
    by = {row["date"]: row["value"] for row in fallback}
    by.update({row["date"]: row["value"] for row in primary})
    return [{"date": date, "value": by[date]} for date in sorted(by)]


def build_pmi() -> dict:
    hit = _read_cache("pmi")
    if hit:
        return hit
    series = []
    with _client() as client:
        man_parts = []
        for dataset, name in PMI_MAN_PARTS:
            doc = _pmi_doc(client, dataset, "in")
            pts = _pmi_points(doc)
            man_parts.append(pts)
            if pts:
                series.append(_pack(f"ism-{dataset}", f"制造业 · {name}", pts, extra={"kind": "man"}))
        svc_parts = []
        for dataset, name in PMI_SVC_PARTS:
            doc = _pmi_doc(client, dataset, "in")
            pts = _pmi_points(doc)
            svc_parts.append(pts)
            if pts:
                series.append(_pack(f"ism-{dataset}", f"服务业 · {name}", pts, extra={"kind": "svc"}))
        man_head = _pmi_points(_pmi_doc(client, "pmi", "pm"))
        svc_head = _pmi_points(_pmi_doc(client, "nm-pmi", "pm"))

    man = _merge_prefer(man_head, _average_points(man_parts))
    svc = _merge_prefer(svc_head, _average_points(svc_parts))
    if man:
        series.insert(0, _pack("ism-man-pmi", "ISM 制造业 PMI", man, extra={"kind": "headline"}))
    if svc:
        series.insert(1 if man else 0, _pack("ism-svc-pmi", "ISM 服务业 PMI", svc, extra={"kind": "headline"}))

    data = {
        "group": "pmi",
        "title": "PMI",
        "unit": "指数，50 为荣枯线",
        "source": "ISM · DBnomics（制造业 PMI 由分项平均补全缺失）",
        "updated_at": _now(),
        "default_ids": [row["id"] for row in series if row.get("kind") == "headline"],
        "series": series,
    }
    _write_cache("pmi", data)
    return data


def build_group(group: str, detail: bool = False) -> dict:
    if group == "cpi":
        return build_cpi()
    if group == "ppi":
        return build_ppi()
    if group == "pce":
        return build_pce()
    if group == "nfp":
        return build_nfp(detail=detail)
    if group == "pmi":
        return build_pmi()
    if group == "treasury":
        return build_treasury()
    raise ValueError(group)
