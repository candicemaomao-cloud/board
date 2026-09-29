"""Kenneth French Fama-French 日度因子下载与解析（带本地缓存）。"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import httpx

CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "fama_french"
FF3_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_daily_CSV.zip"
FF5_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
UA = {"User-Agent": "Mozilla/5.0 (research board-risk)"}


def _download_zip(url: str, cache_name: str) -> bytes:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / cache_name
    if path.exists() and path.stat().st_size > 1000:
        return path.read_bytes()
    with httpx.Client(timeout=60.0, headers=UA, follow_redirects=True) as client:
        res = client.get(url)
        res.raise_for_status()
        data = res.content
    path.write_bytes(data)
    return data


def _parse_factor_csv(raw_zip: bytes) -> dict[str, dict[str, float]]:
    """
    返回 {YYYY-MM-DD: {Mkt-RF, SMB, HML, ... RF}}，因子为小数（原表为百分数）。
    """
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise ValueError("zip 内无 CSV")
        text = zf.read(names[0]).decode("latin-1", errors="replace")

    rows = text.splitlines()
    # skip header junk until a line that looks like column names with Mkt
    start = 0
    for i, line in enumerate(rows):
        low = line.lower()
        if "mkt" in low and "rf" in low:
            start = i
            break
    reader = csv.DictReader(io.StringIO("\n".join(rows[start:])))
    out: dict[str, dict[str, float]] = {}
    for row in reader:
        # first column often unnamed / Date
        keys = list(row.keys())
        if not keys:
            continue
        date_key = keys[0]
        date_raw = (row.get(date_key) or "").strip()
        if not date_raw.isdigit() or len(date_raw) != 8:
            # end of daily block (monthly section starts)
            if out:
                break
            continue
        y, m, d = date_raw[:4], date_raw[4:6], date_raw[6:8]
        iso = f"{y}-{m}-{d}"
        factors: dict[str, float] = {}
        for k, v in row.items():
            if k == date_key:
                continue
            name = (k or "").strip()
            if not name:
                continue
            try:
                factors[name] = float(v.strip()) / 100.0
            except (TypeError, ValueError):
                continue
        if factors:
            out[iso] = factors
    return out


def load_ff_factors(kind: str = "5") -> dict[str, dict[str, float]]:
    """kind: '3' or '5'."""
    if kind == "3":
        raw = _download_zip(FF3_URL, "ff3_daily.zip")
    else:
        raw = _download_zip(FF5_URL, "ff5_daily.zip")
    return _parse_factor_csv(raw)


# 粗略 sector → 行业 ETF（用于行业暴露）
SECTOR_ETF = {
    "technology": "XLK",
    "information technology": "XLK",
    "communication services": "XLC",
    "consumer cyclical": "XLY",
    "consumer discretionary": "XLY",
    "consumer defensive": "XLP",
    "consumer staples": "XLP",
    "energy": "XLE",
    "financial services": "XLF",
    "financials": "XLF",
    "healthcare": "XLV",
    "health care": "XLV",
    "industrials": "XLI",
    "basic materials": "XLB",
    "materials": "XLB",
    "real estate": "XLRE",
    "utilities": "XLU",
}


def sector_etf(sector: str | None) -> str | None:
    if not sector:
        return None
    return SECTOR_ETF.get(sector.strip().lower())
