"""扫 backend/app/user_arbs/*.py，当成套利策略。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from app.services.arb_strategy import (
    ArbError,
    _clip_bt_days,
    _clip_float,
    _clip_int,
    _clip_notional,
    _kind,
    _opt_float,
    _tf,
    run_arb,
    to_out,
)
from app.services.ohlc import fetch_closes
from app.services.user_research import wash_bars

DIR = Path(__file__).resolve().parents[1] / "user_arbs"
ID_PREFIX = "u_"


class ArbFileCtx:
    """传给手写 run(ctx)。"""

    def __init__(self, row: SimpleNamespace, spec: dict, slug: str):
        self.row = row
        self.spec = spec
        self.slug = slug
        self.STRATEGY = spec

    def closes(self, symbol: str, timeframe: str | None = None) -> dict:
        return fetch_closes(symbol, timeframe or self.row.timeframe)

    def bars(self, symbol: str, timeframe: str | None = None) -> list[dict]:
        return wash_bars(self.closes(symbol, timeframe))

    def engine(self, **overrides):
        if not overrides:
            return run_arb(self.row)
        merged = {**self.spec, **overrides}
        return run_arb(_row_from_spec(merged, self.slug, custom_run=True))


def is_file_id(arb_id) -> bool:
    return str(arb_id or "").startswith(ID_PREFIX)


def file_slug(arb_id) -> str:
    return str(arb_id)[len(ID_PREFIX) :]


def list_file_arbs() -> list[dict]:
    items = []
    if not DIR.is_dir():
        return items
    for path in sorted(DIR.glob("*.py")):
        if path.name.startswith("_") or path.name == "__init__.py":
            continue
        try:
            items.append(file_out(_load_row(path.stem)))
        except Exception as exc:
            items.append(
                {
                    "id": f"{ID_PREFIX}{path.stem}",
                    "name": path.stem,
                    "notes": str(exc),
                    "source": "file",
                    "file": path.name,
                    "error": str(exc),
                    "leg_a": "",
                    "leg_b": "",
                    "factors": [],
                    "text": f"文件有错：{exc}",
                    "timeframe": "1d",
                    "timeframe_label": "D线",
                }
            )
    return items


def run_file_arb(arb_id: str) -> dict:
    slug = file_slug(arb_id)
    path = DIR / f"{slug}.py"
    if not path.is_file():
        raise ArbError(f"找不到手写策略 {path.name}，放到 {DIR} 下")
    mod = _load_module(path)
    spec = getattr(mod, "STRATEGY", None)
    if not isinstance(spec, dict):
        raise ArbError(f"{path.name} 里要有 STRATEGY = {{...}}")
    row = _row_from_spec(spec, slug, custom_run=callable(getattr(mod, "run", None)))
    runner = getattr(mod, "run", None)
    if callable(runner):
        out = runner(ArbFileCtx(row, spec, slug))
        if not isinstance(out, dict):
            raise ArbError(f"{path.name} 的 run(ctx) 要返回 dict，或 return ctx.engine()")
        out.setdefault("strategy", file_out(row))
        return out
    result = run_arb(row)
    result["strategy"] = file_out(row)
    return result


def _load_row(slug: str) -> SimpleNamespace:
    path = DIR / f"{slug}.py"
    if not path.is_file():
        raise ArbError(f"找不到 {path.name}")
    mod = _load_module(path)
    spec = getattr(mod, "STRATEGY", None)
    if not isinstance(spec, dict):
        raise ArbError(f"{path.name} 里要有 STRATEGY = {{...}}")
    return _row_from_spec(spec, slug, custom_run=callable(getattr(mod, "run", None)))


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"user_arbs_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ArbError(f"无法加载 {path.name}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _row_from_spec(spec: dict, slug: str, custom_run: bool = False) -> SimpleNamespace:
    name = (spec.get("name") or slug).strip()[:64] or slug
    a = (spec.get("leg_a") or "").strip().upper()
    b = (spec.get("leg_b") or "").strip().upper()
    factors = spec.get("factors")
    if isinstance(factors, str):
        factors = [p.strip().upper() for p in factors.replace(",", "+").split("+") if p.strip()]
    elif isinstance(factors, list):
        factors = [str(x).strip().upper() for x in factors if str(x).strip()]
    else:
        factors = [b] if b else []
    factors = [h for h in factors if h and h != a]
    if not a:
        raise ArbError("STRATEGY 要写 leg_a")
    if not factors:
        raise ArbError("STRATEGY 至少要有一个对冲：leg_b 或 factors")
    packed = json.dumps(factors, ensure_ascii=False)
    row = SimpleNamespace(
        id=f"{ID_PREFIX}{slug}",
        name=name,
        notes=spec.get("notes") or f"手写 {slug}.py",
        kind=_kind(spec.get("kind")),
        timeframe=_tf(spec.get("timeframe")),
        leg_a=a,
        leg_b=factors[0],
        lookback=_clip_int(spec.get("lookback"), 20, 500, 60),
        entry_z=_clip_float(spec.get("entry_z"), 0.5, 6, 2.0),
        exit_z=_clip_float(spec.get("exit_z"), 0.1, 3, 0.5),
        stop_z=_clip_float(spec.get("stop_z"), 1.5, 12, 3.5),
        beta=_opt_float(spec.get("beta")),
        notional=_clip_notional(spec.get("notional")),
        bt_days=_clip_bt_days(spec.get("bt_days")),
        macro_filter=1 if spec.get("macro_filter") else 0,
        factors=packed,
        updated_at=None,
        source="file",
        file=f"{slug}.py",
        custom_run=custom_run,
    )
    if row.exit_z >= row.entry_z:
        raise ArbError("平仓 z 必须小于开仓 z")
    if row.stop_z <= row.entry_z:
        raise ArbError("止损 z 必须大于开仓 z")
    return row


def file_out(row: SimpleNamespace) -> dict:
    out = to_out(row)
    out["source"] = "file"
    out["file"] = getattr(row, "file", None)
    out["custom_run"] = bool(getattr(row, "custom_run", False))
    return out
