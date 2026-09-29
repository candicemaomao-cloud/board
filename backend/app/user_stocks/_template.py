"""拉数据 → 洗数据 → 写信号。

复制本文件，去掉文件名开头的下划线，就会出现在「指标」列表。
STRATEGY 只是名字和默认代码。真正的策略在 run(ctx)。
套利请写到 backend/app/user_arbs/。
"""

STRATEGY = {
    "name": "学习：拉数洗数金叉",
    "notes": "自己算 EMA，不是页面点选。",
    "symbol": "TSLA",
    "side": "long",
    "timeframe": "1d",
}


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    avg = sum(values[:period]) / period
    out[period - 1] = avg
    k = 2 / (period + 1)
    for i in range(period, len(values)):
        avg = values[i] * k + avg * (1 - k)
        out[i] = avg
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, len(values)):
        delta = values[i] - values[i - 1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def run(ctx):
    # 1. 拉：ctx.bars() 已按页面上填的代码 / 周期取 K 线
    bars = ctx.bars()
    close = [bar["close"] for bar in bars]
    if len(close) < 40:
        return ctx.result(False, "洗完之后 K 线不够")

    # 2. 洗：缺 OHLC、非正价格、时间倒退的已经丢掉。这里只做策略需要的变换。
    fast = ema(close, 5)
    slow = ema(close, 20)
    mom = rsi(close, 14)
    if fast[-1] is None or slow[-1] is None or fast[-2] is None or slow[-2] is None:
        return ctx.result(False, "均线还没热身完")
    if mom[-1] is None:
        return ctx.result(False, "RSI 还没热身完")

    # 3. 策略：金叉且 RSI>50
    golden = fast[-2] <= slow[-2] and fast[-1] > slow[-1]
    hit = golden and mom[-1] > 50
    note = (
        f"EMA5={fast[-1]:.2f} EMA20={slow[-1]:.2f} RSI={mom[-1]:.1f}。"
        f"{'金叉' if golden else '无金叉'}。"
        f"用了 {len(close)} 根洗过的 K 线。"
    )
    return ctx.result(hit, note)


def _standalone():
    import sys
    from pathlib import Path

    backend = Path(__file__).resolve().parents[2]
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from app.services.user_stock_files import preview_module

    preview_module(STRATEGY, run)


if __name__ == "__main__":
    _standalone()
