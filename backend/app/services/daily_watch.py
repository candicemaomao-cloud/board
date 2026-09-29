from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DailyWatch
from app.services.ohlc import OhlcError, fetch_closes

MARKET_DIRECTIONS = ("多", "空", "横盘")
SECTORS = ("银行", "科技", "半导体", "通信", "太空", "医疗", '可选消费','金融','能源','必需消费品','工业','公用事业','房地产','原材料','通讯服务', "其他")


class DailyWatchError(Exception):
    pass


def lookup_quote(symbol: str) -> dict:
    """昨日 OHLC + 现价，用户只填代码，这几个数字自动查——不用手动抄。"""
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise DailyWatchError("请输入股票代码")

    try:
        daily = fetch_closes(symbol, "1d", apply_live=False)
    except OhlcError as exc:
        raise DailyWatchError(str(exc)) from exc
    bars = daily.get("ohlc_bars") or []
    if not bars:
        raise DailyWatchError(f"{symbol} 拉不到日线数据，检查一下代码对不对")

    today = date.today()
    prev_bar = None
    for b in reversed(bars):
        bar_day = datetime.fromtimestamp(int(b["ts"]), tz=timezone.utc).date()
        if bar_day < today:
            prev_bar = b
            break
    if prev_bar is None:
        prev_bar = bars[-1]

    current_price = None
    try:
        live = fetch_closes(symbol, "5m", apply_live=True)
        current_price = live.get("price")
    except OhlcError:
        pass
    if current_price is None:
        current_price = bars[-1].get("close")

    return {
        "symbol": symbol,
        "prev_close": prev_bar.get("close"),
        "prev_open": prev_bar.get("open"),
        "prev_low": prev_bar.get("low"),
        "prev_high": prev_bar.get("high"),
        "current_price": current_price,
    }


def refresh_price(db: Session, row: DailyWatch) -> DailyWatch:
    """只更新价格这几项（昨日OHLC + 现价），跟表单里"查询"按钮用的是同一个
    lookup_quote，别的字段（走向/Gamma/回归线等）原样不动。"""
    q = lookup_quote(row.symbol)
    row.prev_close = q["prev_close"]
    row.prev_open = q["prev_open"]
    row.prev_low = q["prev_low"]
    row.prev_high = q["prev_high"]
    row.current_price = q["current_price"]
    db.commit()
    db.refresh(row)
    return row


def refresh_options(db: Session, row: DailyWatch) -> tuple[DailyWatch, float | None]:
    """只更新期权走向 + Max Pain + 隐含预期区间，取期权分析最近一期到期日的数据，
    不动价格/回归线/手动填的买入价等其他字段。

    隐含预期区间用的是"期权分析"页面「隐含预期区间」那一块的下方/上方
    （todays_expected_range_skewed 算出来的 ~68% 概率区间，Call/Put IV 分开算，
    上下不对称），不是 Gamma Flip 点——两者都是"期权分析"页面上的数字，但含义
    不一样：Gamma Flip 是做市商对冲行为切换的价位，隐含预期区间是市场对涨跌
    空间的定价，这里存的是后者。

    走向的判断跟"期权分析"页面上偏斜(skew)的解释一致：偏斜为正——市场愿意为
    下跌保护多付钱，情绪偏空；偏斜为负——情绪偏多；接近 0 说不清楚，按横盘处理
    （±0.5% 当作"接近0"的缓冲区，避免噪音让方向来回跳）。

    偏斜是实时波动的数字（尤其流动性一般的标的，ATM 报价 tick 与 tick 之间就
    可能变号），跟"期权分析"页面分开点开看到的数字未必是同一时刻抓的，看起来
    对不上很正常——不是这里的判断逻辑错了。返回值里带上这次算出来的 skew_pct，
    调用方可以直接展示这个数字，方便用户自己核对当次判断依据，而不用切页面去
    比对一个已经过时的截图。
    """
    from app.user_stocks.options import analyze_option_chain

    try:
        data = analyze_option_chain(row.symbol)
    except Exception as exc:
        raise DailyWatchError(f"{row.symbol} 期权分析失败：{exc}") from exc

    skew_pct = None
    iv = data.get("iv")
    if iv and iv.get("skew_pct") is not None:
        skew_pct = iv["skew_pct"]
        if skew_pct > 0.005:
            row.market_direction = "空"
        elif skew_pct < -0.005:
            row.market_direction = "多"
        else:
            row.market_direction = "横盘"

    max_pain = data.get("max_pain")
    if max_pain and max_pain.get("strike") is not None:
        row.max_pain = max_pain["strike"]

    expected_range = data.get("expected_range")
    if expected_range and expected_range.get("expected_low") is not None and expected_range.get("expected_high") is not None:
        row.gamma_low = expected_range["expected_low"]
        row.gamma_high = expected_range["expected_high"]

    db.commit()
    db.refresh(row)
    return row, skew_pct


def refresh_regression(db: Session, row: DailyWatch) -> DailyWatch:
    """只更新回归线，取"回归线"页面同一套 Theil-Sen 稳健回归算出的趋势线
    预测价（不是当前价，是趋势线在"现在"这一点的拟合值）。"""
    from app.services.screener import analyze_regression

    try:
        data = analyze_regression(row.symbol)
    except Exception as exc:
        raise DailyWatchError(f"{row.symbol} 回归线计算失败：{exc}") from exc
    row.regression_line = data.get("predicted_price")
    db.commit()
    db.refresh(row)
    return row


def list_watches(db: Session) -> list[DailyWatch]:
    rows = list(db.scalars(select(DailyWatch)))
    rows.sort(key=lambda r: r.symbol)
    return rows


def get_watch(db: Session, watch_id: int) -> DailyWatch | None:
    return db.get(DailyWatch, watch_id)


def _apply(row: DailyWatch, data: dict) -> None:
    if "symbol" in data:
        symbol = (data["symbol"] or "").strip().upper()
        if not symbol:
            raise DailyWatchError("请填写股票代码")
        row.symbol = symbol
    if "market_direction" in data:
        direction = data["market_direction"] or "横盘"
        if direction not in MARKET_DIRECTIONS:
            raise DailyWatchError("市场走向只能是 多/空/横盘")
        row.market_direction = direction
    if "sector" in data:
        sector = data["sector"] or "其他"
        if sector not in SECTORS:
            raise DailyWatchError(f"板块只能是 {'/'.join(SECTORS)}")
        row.sector = sector
    for key in (
        "prev_close", "prev_open", "prev_low", "prev_high", "current_price",
        "gamma_low", "gamma_high", "regression_line", "max_pain",
        "short_entry_price", "long_entry_price",
    ):
        if key in data:
            setattr(row, key, data[key])
    if "is_potential" in data:
        row.is_potential = 1 if data["is_potential"] else 0
    if "events" in data:
        row.events = (data["events"] or "").strip() or None


def create_watch(db: Session, data: dict) -> DailyWatch:
    row = DailyWatch(symbol="", market_direction="横盘")
    _apply(row, data)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_watch(db: Session, row: DailyWatch, data: dict) -> DailyWatch:
    _apply(row, data)
    db.commit()
    db.refresh(row)
    return row


def delete_watch(db: Session, row: DailyWatch) -> None:
    db.delete(row)
    db.commit()
