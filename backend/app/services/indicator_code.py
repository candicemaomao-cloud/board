"""自定义指标代码约定：compute(bars) 返回 true / false（或每根 K 线的 bool 列表）。"""

from __future__ import annotations

CODE_TEMPLATE = '''def compute(bars):
    """
    自定义指标函数约定：

    入参 bars: list[dict]
      每根 K 线至少包含:
        open  (float)  开盘
        high  (float)  最高
        low   (float)  最低
        close (float)  收盘
      可选: volume, time / datetime

    返回值（二选一）:
      1) bool
         — 只判断「最新一根」是否成立，例如 True / False
      2) list[bool]
         — 与 bars 等长，每一根是否成立（用于指标验证逐根统计）

    下面示例：最新收盘站上近 20 根最高价（不含本根）→ True
    """
    if len(bars) < 21:
        return False
    closes = [float(b["close"]) for b in bars]
    last = closes[-1]
    prior_high = max(closes[-21:-1])
    return last > prior_high
'''


class IndicatorCodeError(ValueError):
    pass


def is_code_formula(formula: dict | None) -> bool:
    return bool(formula) and str(formula.get("kind") or "").lower() == "code"


def _load_compute(code: str):
    code = (code or "").strip()
    if not code:
        raise IndicatorCodeError("请填写指标函数代码")
    if "def compute" not in code:
        raise IndicatorCodeError("代码里需要定义 compute(bars) 函数")
    try:
        compiled = compile(code, "<indicator>", "exec")
        ns: dict = {}
        exec(compiled, ns, ns)  # noqa: S102 — 用户自写指标，仅本机使用
    except SyntaxError as exc:
        raise IndicatorCodeError(f"代码语法错误：{exc.msg}（第 {exc.lineno} 行）") from exc
    except Exception as exc:  # noqa: BLE001
        raise IndicatorCodeError(f"代码无法加载：{exc}") from exc
    fn = ns.get("compute")
    if not callable(fn):
        raise IndicatorCodeError("代码里需要可调用的 compute(bars)")
    return fn


def normalize_code_formula(raw: dict | None) -> dict:
    data = raw or {}
    code = str(data.get("code") or "").strip()
    _load_compute(code)
    return {"kind": "code", "code": code}


def run_compute(code: str, bars: list[dict]) -> bool | list[bool]:
    """执行 compute，返回 bool（最新一根）或与 bars 等长的 list[bool]。"""
    fn = _load_compute(code)
    try:
        out = fn(bars)
    except Exception as exc:  # noqa: BLE001
        raise IndicatorCodeError(f"compute 执行失败：{exc}") from exc
    return _normalize_signal(out, len(bars))


def signal_series(code: str, bars: list[dict]) -> list[bool]:
    """统一成与 bars 等长的 bool 序列，便于逐根统计。"""
    out = run_compute(code, bars)
    if isinstance(out, list):
        return out
    n = len(bars)
    if n <= 0:
        return []
    # 单 bool：只标记最后一根
    return [False] * (n - 1) + [bool(out)]


def last_signal(code: str, bars: list[dict]) -> bool:
    out = run_compute(code, bars)
    if isinstance(out, list):
        if not out:
            return False
        return bool(out[-1])
    return bool(out)


def _normalize_signal(out, n: int) -> bool | list[bool]:
    if isinstance(out, bool):
        return out
    if isinstance(out, (int, float)) and out in (0, 1):
        return bool(out)
    if isinstance(out, list):
        if len(out) != n:
            raise IndicatorCodeError(f"返回列表长度应为 {n}，实际 {len(out)}")
        return [bool(x) for x in out]
    raise IndicatorCodeError("返回值必须是 True/False，或与 K 线等长的 bool 列表")
