"""拉数据 → 洗数据 → 写价差信号。

复制本文件，去掉文件名开头的下划线，就会出现在套利策略列表。
STRATEGY 只是名字和默认参数。真正的研究在 run(ctx)。
单票请写到 backend/app/user_stocks/。
"""

from statistics import mean, pstdev

from app.services.arb_strategy import ArbError

STRATEGY = {
    "name": "学习：拉数洗数价差",
    "notes": "自己拉两腿、对齐、算 z，再交给引擎开平仓。",
    "kind": "ols",
    "timeframe": "1d",
    "leg_a": "QQQ",
    "leg_b": "TLT",
    "factors": ["TLT"],
    "lookback": 60,
    "entry_z": 2.0,
    "exit_z": 0.5,
    "stop_z": 3.5,
    "notional": 10000,
    "bt_days": 365,
}


def run(ctx):
    a = ctx.STRATEGY["leg_a"]
    b = ctx.STRATEGY["leg_b"]
    bars_a = ctx.bars(a)
    bars_b = ctx.bars(b)
    by_b = {bar["ts"]: bar["close"] for bar in bars_b}
    paired = [(bar["close"], by_b[bar["ts"]]) for bar in bars_a if bar["ts"] in by_b]
    if len(paired) < 80:
        raise ArbError("对齐后 K 线不够")

    # 简单检查：最后 60 根价差 z，方便你在写规则前先看数
    lookback = int(ctx.STRATEGY["lookback"])
    px_a = [p[0] for p in paired]
    px_b = [p[1] for p in paired]
    ratio = [x / y for x, y in zip(px_a[-lookback:], px_b[-lookback:])]
    mu, sd = mean(ratio), pstdev(ratio) if len(ratio) > 1 else 0.0
    z = 0.0 if sd < 1e-12 else (ratio[-1] - mu) / sd
    # 真正开平仓仍用引擎（滚动 OLS）。上面这段是给你自己看数的。
    out = ctx.engine()
    note = out.get("hypothesis") or ""
    out["hypothesis"] = f"洗数后对齐 {len(paired)} 根，近窗比值 z={z:.2f}。{note}"
    return out
