"""扫 backend/app/user_stocks/*.py，当成单票指标策略。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.services.ohlc import fetch_closes
from app.services.strategy_calc import calculate
from app.services.strategy_plans import StrategyError, normalize_side, normalize_timeframe
from app.services.strategy_spec import empty_formula, formula_text, normalize_formula
from app.services.user_research import wash_bars

DIR = Path(__file__).resolve().parents[1] / "user_stocks"
ID_PREFIX = "s_"


class StockFileCtx:
    def __init__(self, plan: dict, spec: dict, slug: str, symbol: str, timeframe: str):
        self.plan = plan
        self.spec = spec
        self.slug = slug
        self.STRATEGY = spec
        self.symbol = symbol
        self.timeframe = timeframe

    def pull(self, symbol: str | None = None, timeframe: str | None = None) -> dict:
        code = (symbol or self.symbol or self.plan.get("symbol") or "").strip().upper()
        if not code:
            raise StrategyError("请填写股票代码")
        return fetch_closes(code, timeframe or self.timeframe or self.plan["timeframe"])

    def bars(self, symbol: str | None = None, timeframe: str | None = None) -> list[dict]:
        return wash_bars(self.pull(symbol, timeframe))

    def engine(self, symbol: str | None = None, timeframe: str | None = None):
        code = (symbol or self.symbol or self.plan.get("symbol") or "").strip().upper()
        if not code:
            raise StrategyError("请填写股票代码")
        tf = timeframe or self.timeframe or self.plan["timeframe"]
        formula = self.plan.get("formula") if self.plan.get("has_formula") else None
        out = calculate(code, tf, formula, {}, {}, trade_side=self.plan.get("side"))
        out["strategy"] = self.plan
        return out

    def result(self, match: bool, note: str = "", **extra):
        """自己算完信号后交给页面：命中 / 未命中 + 一句说明。"""
        if getattr(self, "cli", False):
            return {"match": bool(match), "explain": {"text": note}, "strategy": self.plan, **extra}
        out = self.engine()
        out["match"] = bool(match)
        out["explain"] = {"text": note, "ok": bool(match), "fail": [] if match else [note]}
        out.update(extra)
        return out


def is_file_id(plan_id) -> bool:
    return str(plan_id or "").startswith(ID_PREFIX)


def file_slug(plan_id) -> str:
    return str(plan_id)[len(ID_PREFIX) :]


def list_file_stocks() -> list[dict]:
    items = []
    if not DIR.is_dir():
        return items
    for path in sorted(DIR.glob("*.py")):
        if path.name.startswith("_") or path.name == "__init__.py":
            continue
        try:
            items.append(_load_plan(path.stem))
        except Exception as exc:
            items.append(
                {
                    "id": f"{ID_PREFIX}{path.stem}",
                    "name": path.stem,
                    "notes": str(exc),
                    "side": "long",
                    "timeframe": "1d",
                    "formula": {},
                    "text": f"文件有错：{exc}",
                    "source": "file",
                    "file": path.name,
                    "error": str(exc),
                    "symbol": "",
                }
            )
    return items


def get_file_plan(plan_id: str) -> dict:
    slug = file_slug(plan_id)
    path = DIR / f"{slug}.py"
    if not path.is_file():
        raise StrategyError(f"找不到手写策略 {path.name}，放到 {DIR} 下")
    return _load_plan(slug)


def run_file_stock(plan_id: str, symbol: str | None, timeframe: str | None):
    slug = file_slug(plan_id)
    path = DIR / f"{slug}.py"
    if not path.is_file():
        raise StrategyError(f"找不到手写策略 {path.name}，放到 {DIR} 下")
    mod = _load_module(path)
    spec = getattr(mod, "STRATEGY", None)
    if not isinstance(spec, dict):
        raise StrategyError(f"{path.name} 里要有 STRATEGY = {{...}}，至少写 name / symbol")
    runner = getattr(mod, "run", None)
    if not callable(runner) and not spec.get("indicators") and not spec.get("formula"):
        raise StrategyError(f"{path.name} 要写 run(ctx)，或写 indicators 配置")
    plan = _plan_from_spec(spec, slug, custom_run=callable(runner))
    code = (symbol or plan.get("symbol") or "").strip().upper()
    tf = timeframe or plan["timeframe"]
    ctx = StockFileCtx(plan, spec, slug, code, tf)
    if callable(runner):
        out = runner(ctx)
        if not isinstance(out, dict):
            raise StrategyError(f"{path.name} 的 run(ctx) 要返回 dict，或 return ctx.result(命中, '说明')")
        out.setdefault("strategy", plan)
        return out
    if not code:
        raise StrategyError("请填写股票代码")
    return ctx.engine(code, tf)


def _load_plan(slug: str) -> dict:
    path = DIR / f"{slug}.py"
    if not path.is_file():
        raise StrategyError(f"找不到 {path.name}")
    mod = _load_module(path)
    spec = getattr(mod, "STRATEGY", None)
    if not isinstance(spec, dict):
        raise StrategyError(f"{path.name} 里要有 STRATEGY = {{...}}")
    return _plan_from_spec(spec, slug, custom_run=callable(getattr(mod, "run", None)))


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"user_stocks_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise StrategyError(f"无法加载 {path.name}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _formula_from_spec(spec: dict) -> dict:
    if spec.get("formula"):
        return normalize_formula(spec["formula"])
    ids = spec.get("indicators") or []
    if not ids:
        raise StrategyError("STRATEGY 要写 indicators 或 formula")
    clauses = []
    for raw in ids:
        if isinstance(raw, dict):
            cid = str(raw.get("id") or "").strip()
            if not cid:
                continue
            clauses.append({"kind": "indicator", "id": cid, "not": bool(raw.get("not"))})
        else:
            cid = str(raw).strip()
            if cid:
                clauses.append({"kind": "indicator", "id": cid, "not": False})
    if not clauses:
        raise StrategyError("indicators 不能为空")
    return normalize_formula(
        {
            "join": spec.get("join") or "and",
            "groups": [{"join": spec.get("group_join") or "and", "clauses": clauses}],
            "targets": spec.get("targets") or [],
        }
    )


def _plan_from_spec(spec: dict, slug: str, custom_run: bool = False) -> dict:
    name = (spec.get("name") or slug).strip()[:64] or slug
    has_formula = bool(spec.get("formula") or spec.get("indicators"))
    if has_formula:
        formula = _formula_from_spec(spec)
        text = formula_text(formula, side=normalize_side(spec.get("side")))
    else:
        if not custom_run:
            raise StrategyError("要么写 run(ctx)，要么写 indicators")
        formula = empty_formula()
        text = spec.get("notes") or "手写：拉数据 → 洗数据 → 信号"
    side = normalize_side(spec.get("side"))
    timeframe = normalize_timeframe(spec.get("timeframe"))
    symbol = str(spec.get("symbol") or "").strip().upper()
    return {
        "id": f"{ID_PREFIX}{slug}",
        "name": name,
        "notes": spec.get("notes") or f"手写 {slug}.py",
        "side": side,
        "timeframe": timeframe,
        "formula": formula,
        "has_formula": has_formula,
        "text": text,
        "created_at": None,
        "updated_at": None,
        "source": "file",
        "file": f"{slug}.py",
        "custom_run": custom_run,
        "symbol": symbol,
    }


def preview_module(spec: dict, run_fn, n: int = 10) -> None:
    """命令行直接跑策略文件时，把洗过的 K 线打到终端。"""
    from datetime import datetime, timezone

    plan = _plan_from_spec(spec, "preview", custom_run=True)
    symbol = plan["symbol"] or "TSLA"
    tf = plan["timeframe"]
    ctx = StockFileCtx(plan, spec, "preview", symbol, tf)
    ctx.cli = True
    raw = ctx.pull()
    bars = wash_bars(raw)
    print(f"{symbol}  {tf}  来源={raw.get('source')}  现价={raw.get('price')}  原始={raw.get('bars')}  洗完={len(bars)}")
    if n > 0:
        print(f"{'时间(UTC)':<20} {'开':>10} {'高':>10} {'低':>10} {'收':>10} {'量':>12}")
        for bar in bars[-n:]:
            t = datetime.fromtimestamp(int(bar["ts"]), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            print(
                f"{t:<20} {bar['open']:10.2f} {bar['high']:10.2f} {bar['low']:10.2f} "
                f"{bar['close']:10.2f} {bar['volume']:12.0f}"
            )
    out = run_fn(ctx)
    hit = "命中" if out.get("match") else "未命中"
    note = (out.get("explain") or {}).get("text") or ""
    print(f"{hit}  {note}")

