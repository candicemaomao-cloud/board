from __future__ import annotations

INDICATORS = [
    {"id": "golden", "name": "均线金叉", "group": "趋势", "hint": "快线上穿慢线"},
    {"id": "death", "name": "均线死叉", "group": "趋势", "hint": "快线下穿慢线"},
    {"id": "align_bull", "name": "多头排列", "group": "趋势", "hint": "EMA5 > 10 > 20"},
    {"id": "align_bear", "name": "空头排列", "group": "趋势", "hint": "EMA5 < 10 < 20"},
    {"id": "align_tangle", "name": "均线纠缠", "group": "趋势", "hint": "短均线缠在一起"},
    {"id": "sma20_above", "name": "站上 SMA20", "group": "趋势", "hint": "收盘 > SMA20"},
    {"id": "sma50_above", "name": "站上 SMA50", "group": "趋势", "hint": "收盘 > SMA50"},
    {"id": "sma200_above", "name": "站上 SMA200", "group": "趋势", "hint": "收盘 > SMA200"},
    {"id": "sma20_gt_sma50", "name": "SMA20 > SMA50", "group": "趋势", "hint": "中短均线多头"},
    {"id": "sma50_gt_sma200", "name": "SMA50 > SMA200", "group": "趋势", "hint": "中长均线多头"},
    {"id": "ema20_above", "name": "站上 EMA20", "group": "趋势", "hint": "收盘 > EMA20"},
    {"id": "ema50_above", "name": "站上 EMA50", "group": "趋势", "hint": "收盘 > EMA50"},
    {"id": "ema20_up", "name": "EMA20 向上", "group": "趋势", "hint": "EMA20 本根抬高"},
    {"id": "macd_golden", "name": "MACD 金叉", "group": "趋势", "hint": "MACD 上穿信号线"},
    {"id": "macd_death", "name": "MACD 死叉", "group": "趋势", "hint": "MACD 下穿信号线"},
    {"id": "macd_pos", "name": "MACD > 0", "group": "趋势", "hint": "零轴上方"},
    {"id": "macd_neg", "name": "MACD < 0", "group": "趋势", "hint": "零轴下方"},
    {"id": "adx_lt15", "name": "ADX < 15 震荡", "group": "趋势", "hint": "几乎没有趋势"},
    {"id": "adx_15_20", "name": "ADX 15–20 弱趋势", "group": "趋势", "hint": "趋势刚萌芽"},
    {"id": "adx_gt20", "name": "ADX > 20 有趋势", "group": "趋势", "hint": "开始有方向"},
    {"id": "adx_gt25", "name": "ADX > 25 趋势明确", "group": "趋势", "hint": "趋势比较明显"},
    {"id": "adx_gt40", "name": "ADX > 40 强趋势", "group": "趋势", "hint": "趋势很强"},
    {"id": "adx_up", "name": "ADX 上升", "group": "趋势", "hint": "趋势在加强"},
    {"id": "plus_di_lead", "name": "+DI > -DI 多头", "group": "趋势", "hint": "ADX 方向偏多"},
    {"id": "minus_di_lead", "name": "-DI > +DI 空头", "group": "趋势", "hint": "ADX 方向偏空"},
    {"id": "ichimoku_above_cloud", "name": "站上云层", "group": "趋势", "hint": "Ichimoku 价格在云上"},
    {"id": "ichimoku_below_cloud", "name": "跌破云层", "group": "趋势", "hint": "价格在云下"},
    {"id": "ichimoku_tk_golden", "name": "转换线上穿基准线", "group": "趋势", "hint": "Ichimoku TK 金叉"},
    {"id": "ichimoku_tk_death", "name": "转换线下穿基准线", "group": "趋势", "hint": "Ichimoku TK 死叉"},
    {"id": "sar_up", "name": "价格在 SAR 上方", "group": "趋势", "hint": "抛物线多头"},
    {"id": "sar_down", "name": "价格在 SAR 下方", "group": "趋势", "hint": "抛物线空头"},
    {"id": "sar_flip_up", "name": "SAR 翻多", "group": "趋势", "hint": "本根翻到 SAR 上方"},
    {"id": "sar_flip_down", "name": "SAR 翻空", "group": "趋势", "hint": "本根翻到 SAR 下方"},
    {"id": "rsi_gt70", "name": "RSI > 70 超买", "group": "动量", "hint": "动能过热"},
    {"id": "rsi_gt80", "name": "RSI > 80", "group": "动量", "hint": "极端超买"},
    {"id": "rsi_lt30", "name": "RSI < 30 超卖", "group": "动量", "hint": "动能过冷"},
    {"id": "rsi_lt20", "name": "RSI < 20", "group": "动量", "hint": "极端超卖"},
    {"id": "rsi_gt50", "name": "RSI > 50 多头动能", "group": "动量", "hint": "多头占优"},
    {"id": "rsi_lt50", "name": "RSI < 50 空头动能", "group": "动量", "hint": "空头占优"},
    {"id": "rsi_cross_up_30", "name": "RSI 上穿 30", "group": "动量", "hint": "超卖拐点"},
    {"id": "rsi_cross_down_70", "name": "RSI 下穿 70", "group": "动量", "hint": "超买回落"},
    {"id": "rsi_cross_up_50", "name": "RSI 上穿 50", "group": "动量", "hint": "动能转多"},
    {"id": "rsi_cross_down_50", "name": "RSI 下穿 50", "group": "动量", "hint": "动能转空"},
    {"id": "rsi_bull_div", "name": "RSI 底背离", "group": "动量", "hint": "价新低、RSI 未新低"},
    {"id": "rsi_bear_div", "name": "RSI 顶背离", "group": "动量", "hint": "价新高、RSI 未新高"},
    {"id": "macd_hist_up", "name": "MACD 柱放大", "group": "动量", "hint": "柱状图变大"},
    {"id": "macd_hist_down", "name": "MACD 柱缩小", "group": "动量", "hint": "柱状图变小"},
    {"id": "macd_bull_div", "name": "MACD 底背离", "group": "动量", "hint": "价新低、MACD 未新低"},
    {"id": "macd_bear_div", "name": "MACD 顶背离", "group": "动量", "hint": "价新高、MACD 未新高"},
    {"id": "stoch_oversold", "name": "KD 超卖", "group": "动量", "hint": "%K < 20"},
    {"id": "stoch_overbought", "name": "KD 超买", "group": "动量", "hint": "%K > 80"},
    {"id": "stoch_golden", "name": "KD 低位金叉", "group": "动量", "hint": "超卖区 K 上穿 D"},
    {"id": "stoch_death", "name": "KD 高位死叉", "group": "动量", "hint": "超买区 K 下穿 D"},
    {"id": "kdj_golden", "name": "KDJ 本根金叉", "group": "动量", "hint": "这一根 K 刚上穿 D，只算刚叉的那一根"},
    {"id": "kdj_death", "name": "KDJ 本根死叉", "group": "动量", "hint": "这一根 K 刚下穿 D"},
    {"id": "kdj_above", "name": "KDJ 已金叉", "group": "动量", "hint": "K > D，已经过了金叉"},
    {"id": "kdj_below", "name": "KDJ 未金叉", "group": "动量", "hint": "K < D，还没金叉"},
    {"id": "kdj_overbought", "name": "KDJ 超买", "group": "动量", "hint": "J > 100"},
    {"id": "kdj_oversold", "name": "KDJ 超卖", "group": "动量", "hint": "J < 0"},
    {"id": "cci_gt100", "name": "CCI > 100", "group": "动量", "hint": "强势偏离"},
    {"id": "cci_lt_m100", "name": "CCI < -100", "group": "动量", "hint": "弱势偏离"},
    {"id": "cci_cross_up_100", "name": "CCI 上穿 100", "group": "动量", "hint": "进入强势区"},
    {"id": "cci_cross_down_m100", "name": "CCI 下穿 -100", "group": "动量", "hint": "进入弱势区"},
    {"id": "roc_pos", "name": "ROC > 0", "group": "动量", "hint": "涨速为正"},
    {"id": "roc_neg", "name": "ROC < 0", "group": "动量", "hint": "涨速为负"},
    {"id": "roc_up", "name": "ROC 上升", "group": "动量", "hint": "涨速在加快"},
    {"id": "intraday_oversold", "name": "日内超跌", "group": "动量", "hint": "4h 涨跌 z < -2"},
    {"id": "week_oversold", "name": "周内超跌", "group": "动量", "hint": "日线涨跌 z < -2"},
    {"id": "bb_up", "name": "布林通道向上", "group": "波动", "hint": "中轨抬高且开口"},
    {"id": "bb_down", "name": "布林通道向下", "group": "波动", "hint": "中轨下移且开口"},
    {"id": "bb_squeeze", "name": "布林带宽收缩", "group": "波动", "hint": "带宽处在近低位"},
    {"id": "bb_expand", "name": "布林带宽扩张", "group": "波动", "hint": "波动开始放大"},
    {"id": "bb_above_upper", "name": "收盘站上布林上轨", "group": "波动", "hint": "强势突破"},
    {"id": "bb_below_lower", "name": "收盘跌破布林下轨", "group": "波动", "hint": "超跌/破位"},
    {"id": "sideways", "name": "横盘震荡", "group": "波动", "hint": "近期波幅收窄"},
    {"id": "atr_up", "name": "ATR 上升", "group": "波动", "hint": "波动在变大"},
    {"id": "atr_down", "name": "ATR 下降", "group": "波动", "hint": "波动在变小"},
    {"id": "atr_spike", "name": "ATR 突增", "group": "波动", "hint": "ATR > 1.5 倍均线"},
    {"id": "atr_drop", "name": "ATR 突降", "group": "波动", "hint": "ATR < 0.7 倍均线"},
    {"id": "atr_pct_high", "name": "ATR/价格 > 3%", "group": "波动", "hint": "这只票现在很能波动"},
    {"id": "keltner_above", "name": "收盘站上 Keltner 上轨", "group": "波动", "hint": "趋势突破"},
    {"id": "keltner_below", "name": "收盘跌破 Keltner 下轨", "group": "波动", "hint": "向下突破"},
    {"id": "keltner_squeeze", "name": "Keltner 收缩", "group": "波动", "hint": "通道变窄"},
    {"id": "keltner_break_up", "name": "Keltner 向上突破", "group": "波动", "hint": "本根上穿上轨"},
    {"id": "keltner_break_down", "name": "Keltner 向下突破", "group": "波动", "hint": "本根下穿下轨"},
    {"id": "vol_up", "name": "成交量增加", "group": "成交量", "hint": "本根量 > 上一根"},
    {"id": "vol_down", "name": "成交量减少", "group": "成交量", "hint": "本根量 < 上一根"},
    {"id": "vol_high", "name": "放量", "group": "成交量", "hint": "量 > 1.5 倍均量"},
    {"id": "vol_low", "name": "缩量", "group": "成交量", "hint": "量 < 0.6 倍均量"},
    {"id": "obv_up", "name": "OBV 上升", "group": "成交量", "hint": "资金在跟随"},
    {"id": "obv_down", "name": "OBV 下降", "group": "成交量", "hint": "资金在离开"},
    {"id": "obv_high", "name": "OBV 创新高", "group": "成交量", "hint": "近 20 根新高"},
    {"id": "obv_low", "name": "OBV 创新低", "group": "成交量", "hint": "近 20 根新低"},
    {"id": "price_high_obv_flat", "name": "价新高 OBV 没新高", "group": "成交量", "hint": "上涨缺量"},
    {"id": "price_low_obv_flat", "name": "价新低 OBV 没新低", "group": "成交量", "hint": "下跌缺量"},
    {"id": "mfi_gt80", "name": "MFI > 80", "group": "成交量", "hint": "带量超买"},
    {"id": "mfi_lt20", "name": "MFI < 20", "group": "成交量", "hint": "带量超卖"},
    {"id": "mfi_up", "name": "MFI 上升", "group": "成交量", "hint": "资金流入加强"},
    {"id": "mfi_down", "name": "MFI 下降", "group": "成交量", "hint": "资金流入减弱"},
    {"id": "mfi_cross_down_80", "name": "MFI 下穿 80", "group": "成交量", "hint": "超买后回落"},
    {"id": "mfi_cross_up_20", "name": "MFI 上穿 20", "group": "成交量", "hint": "超卖后回流"},
    {"id": "cmf_pos", "name": "CMF > 0 流入", "group": "成交量", "hint": "资金净流入"},
    {"id": "cmf_neg", "name": "CMF < 0 流出", "group": "成交量", "hint": "资金净流出"},
    {"id": "ad_up", "name": "A/D 上升", "group": "成交量", "hint": "累积派发向上"},
    {"id": "ad_down", "name": "A/D 下降", "group": "成交量", "hint": "累积派发向下"},
    {"id": "vwap_above", "name": "价格 > VWAP", "group": "成交量", "hint": "站在成本上方"},
    {"id": "vwap_below", "name": "价格 < VWAP", "group": "成交量", "hint": "落在成本下方"},
    {"id": "vwap_cross_up", "name": "上穿 VWAP", "group": "成交量", "hint": "本根站上 VWAP"},
    {"id": "vwap_cross_down", "name": "下穿 VWAP", "group": "成交量", "hint": "本根跌破 VWAP"},
    {"id": "vwap_up", "name": "VWAP 向上", "group": "成交量", "hint": "成本重心上移"},
    {"id": "vwap_down", "name": "VWAP 向下", "group": "成交量", "hint": "成本重心下移"},
    {"id": "climax_top", "name": "放量见顶", "group": "成交量", "hint": "放量滞涨 / 长上影"},
    {"id": "exhaustion", "name": "空头砸不动了", "group": "成交量", "hint": "下跌放量但不再新低"},
    {"id": "high_5", "name": "5 日新高", "group": "突破", "hint": "日线收盘高于前 5 个交易日最高，适合周线节奏"},
    {"id": "low_5", "name": "5 日新低", "group": "突破", "hint": "日线收盘低于前 5 个交易日最低"},
    {"id": "break_resistance", "name": "突破压力位", "group": "突破", "hint": "收盘上穿最强量能压力或最近摆动高点"},
    {"id": "hold_support", "name": "站稳支撑位", "group": "突破", "hint": "下探最强量能支撑后收阳站回上方"},
    {"id": "donchian5_break_up", "name": "5 日通道向上突破", "group": "突破", "hint": "收盘高于近 5 日最高"},
    {"id": "donchian5_break_down", "name": "5 日通道向下突破", "group": "突破", "hint": "收盘低于近 5 日最低"},
    {"id": "high_20", "name": "20 日新高", "group": "突破", "hint": "本根收盘高于前 20 根最高，是突破，不是靠近高位"},
    {"id": "high_zone_20", "name": "20 日高位", "group": "突破", "hint": "收盘距近 20 日最高 3% 以内，已经在高位，不要求创新高"},
    {"id": "low_20", "name": "20 日新低", "group": "突破", "hint": "收盘低于前 20 根最低"},
    {"id": "low_zone_20", "name": "20 日低位", "group": "突破", "hint": "收盘距近 20 日最低 3% 以内，已经在低位，不要求创新低"},
    {"id": "high_50", "name": "50 日新高", "group": "突破", "hint": "收盘高于前 50 根最高"},
    {"id": "low_50", "name": "50 日新低", "group": "突破", "hint": "收盘低于前 50 根最低"},
    {"id": "high_52w", "name": "52 周新高", "group": "突破", "hint": "日线近一年新高"},
    {"id": "low_52w", "name": "52 周新低", "group": "突破", "hint": "日线近一年新低"},
    {"id": "donchian_break_up", "name": "Donchian 向上突破", "group": "突破", "hint": "收盘高于 20 日通道上沿"},
    {"id": "donchian_break_down", "name": "Donchian 向下突破", "group": "突破", "hint": "收盘低于 20 日通道下沿"},
    {"id": "rs_spy_20_win", "name": "20 日跑赢 SPY", "group": "相对强弱", "hint": "相对大盘走强"},
    {"id": "rs_spy_20_lose", "name": "20 日跑输 SPY", "group": "相对强弱", "hint": "相对大盘走弱"},
    {"id": "rs_qqq_20_win", "name": "20 日跑赢 QQQ", "group": "相对强弱", "hint": "相对纳指走强"},
    {"id": "rs_qqq_20_lose", "name": "20 日跑输 QQQ", "group": "相对强弱", "hint": "相对纳指走弱"},
    {"id": "rs_spy_60_win", "name": "60 日跑赢 SPY", "group": "相对强弱", "hint": "中期相对大盘"},
    {"id": "rs_qqq_60_win", "name": "60 日跑赢 QQQ", "group": "相对强弱", "hint": "中期相对纳指"},
    {"id": "rs_spy_120_win", "name": "120 日跑赢 SPY", "group": "相对强弱", "hint": "更长周期相对强弱"},
]

BUY_TARGETS = [
    {"id": "dip_low5", "name": "5日最低", "hint": "近 5 个交易日最低价"},
    {"id": "dip_low10", "name": "10日最低", "hint": "近 10 个交易日最低价"},
    {"id": "dip_low20", "name": "20日最低", "hint": "近 20 个交易日最低价"},
    {"id": "tgt_high5", "name": "5日最高", "hint": "近 5 个交易日最高价"},
    {"id": "tgt_high10", "name": "10日最高", "hint": "近 10 个交易日最高价"},
    {"id": "tgt_high20", "name": "20日最高", "hint": "近 20 个交易日最高价"},
    {"id": "dip_week_low", "name": "周线最低", "hint": "本周最低价"},
    {"id": "dip_month_low", "name": "月线最低", "hint": "本月最低价"},
    {"id": "dip_ema5", "name": "EMA5", "hint": "EMA5"},
    {"id": "dip_ema10", "name": "EMA10", "hint": "EMA10"},
    {"id": "dip_ema20", "name": "EMA20", "hint": "EMA20"},
]

INDICATOR_MAP = {row["id"]: row for row in INDICATORS}
BUY_TARGET_MAP = {row["id"]: row for row in BUY_TARGETS}
BUY_TARGET_KEYS = {
    "dip_low5": ("5日最低", "low5"),
    "dip_low10": ("10日最低", "low10"),
    "dip_low20": ("20日最低", "low20"),
    "tgt_high5": ("5日最高", "high5"),
    "tgt_high10": ("10日最高", "high10"),
    "tgt_high20": ("20日最高", "high20"),
    "dip_week_low": ("周线最低", "week_low"),
    "dip_month_low": ("月线最低", "month_low"),
    "dip_ema5": ("EMA5", "ema5"),
    "dip_ema10": ("EMA10", "ema10"),
    "dip_ema20": ("EMA20", "ema20"),
}
JOIN_OPS = {"and", "or"}


def empty_formula() -> dict:
    return {
        "join": "and",
        "groups": [
            {"join": "and", "clauses": [{"kind": "indicator", "id": "golden", "not": False}]},
        ],
        "targets": [],
    }


def _clause(raw: dict) -> dict:
    data = raw or {}
    kind = str(data.get("kind") or "indicator").strip().lower()
    if kind == "strategy":
        try:
            sid = int(data.get("id") or data.get("strategy_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        if sid <= 0:
            raise ValueError("引用的策略无效")
        return {"kind": "strategy", "id": sid, "not": bool(data.get("not"))}
    cid = str(data.get("id") or "").strip()
    if cid not in INDICATOR_MAP:
        raise ValueError(f"未知指标：{cid or '空'}")
    return {"kind": "indicator", "id": cid, "not": bool(data.get("not"))}


def _group(raw: dict) -> dict:
    join = str((raw or {}).get("join") or "and").lower()
    if join not in JOIN_OPS:
        raise ValueError("组内连接只能是 and 或 or")
    clauses = [_clause(item) for item in (raw or {}).get("clauses") or []]
    if not clauses:
        raise ValueError("每一组至少要有一个指标或策略")
    return {"join": join, "clauses": clauses}


def _target(raw: dict | str | None) -> dict:
    if isinstance(raw, str):
        cid = raw.strip()
    else:
        cid = str((raw or {}).get("id") or "").strip()
    if cid not in BUY_TARGET_MAP:
        raise ValueError(f"未知买点：{cid or '空'}")
    return {"id": cid}


def normalize_formula(raw: dict | None) -> dict:
    data = raw or empty_formula()
    join = str(data.get("join") or "and").lower()
    if join not in JOIN_OPS:
        raise ValueError("组间连接只能是 and 或 or")
    groups = [_group(item) for item in data.get("groups") or []]
    if not groups:
        raise ValueError("至少要有一组")
    seen = set()
    targets = []
    for item in data.get("targets") or []:
        row = _target(item)
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        targets.append(row)
        if len(targets) >= 2:
            break
    return {"join": join, "groups": groups, "targets": targets}


def ref_ids(formula: dict | None) -> set[int]:
    ids: set[int] = set()
    for group in (formula or {}).get("groups") or []:
        for clause in group.get("clauses") or []:
            if clause.get("kind") == "strategy":
                try:
                    ids.add(int(clause["id"]))
                except (TypeError, ValueError):
                    continue
    return ids


def clause_text(clause: dict, names: dict | None = None) -> str:
    names = names or {}
    if clause.get("kind") == "strategy":
        name = names.get(int(clause["id"])) or f"策略{clause['id']}"
    else:
        name = INDICATOR_MAP.get(str(clause.get("id")), {}).get("name") or str(clause.get("id"))
    return f"非{name}" if clause.get("not") else name


def group_text(group: dict, names: dict | None = None) -> str:
    joiner = " 且 " if group.get("join") == "and" else " 或 "
    parts = [clause_text(item, names) for item in group.get("clauses") or []]
    text = joiner.join(parts)
    return f"（{text}）" if len(parts) > 1 else text


def formula_text(formula: dict | None, names: dict | None = None, side: str | None = None) -> str:
    data = formula or empty_formula()
    joiner = " 且 " if data.get("join") == "and" else " 或 "
    text = joiner.join(group_text(group, names) for group in data.get("groups") or []) or "—"
    labels = [
        BUY_TARGET_MAP.get(str(item.get("id")), {}).get("name")
        for item in data.get("targets") or []
    ]
    labels = [name for name in labels if name]
    if labels:
        verb = "最优卖出" if str(side or "").strip().lower() == "short" else "最优买入"
        text = f"{text} · {verb} {' / '.join(labels)}"
    return text


def _px(value) -> float | None:
    try:
        if value is None:
            return None
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _snap_val(snap: dict | None, key: str) -> float | None:
    if not snap:
        return None
    if key == "resistance":
        return _px((snap.get("resistance") or {}).get("price"))
    if key == "support":
        return _px((snap.get("support") or {}).get("price"))
    if key == "bb_upper":
        return _px((snap.get("bollinger") or {}).get("upper"))
    if key == "bb_middle":
        return _px((snap.get("bollinger") or {}).get("middle"))
    if key == "bb_lower":
        return _px((snap.get("bollinger") or {}).get("lower"))
    if key == "range_high":
        return _px(snap.get("range_high") or (snap.get("range") or {}).get("high"))
    if key == "range_low":
        return _px(snap.get("range_low") or (snap.get("range") or {}).get("low"))
    return _px(snap.get(key))


def _buy_action(side: str, last: float | None, price: float) -> str:
    if side == "below":
        return "avoid"
    if last is None:
        return "break"
    return "break" if last < price else "dip"


def _level_items(last: float | None, pairs: list[tuple[str, object]], side: str = "above", limit: int = 2) -> list[dict]:
    rows = []
    seen: set[tuple[str, float]] = set()
    for label, value in pairs:
        price = _px(value)
        if price is None:
            continue
        key = (label, round(price, 2))
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "label": label,
            "price": price,
            "side": side,
            "action": _buy_action(side, last, price),
            "gap": None if last is None else round(price - last, 4),
        })
    if last is not None and len(rows) > limit:
        rows.sort(key=lambda row: abs(row["price"] - last))
    return rows[:limit]


def _levels_from_keys(snap: dict | None, keys: list[tuple[str, str]], side: str = "above") -> list[dict]:
    last = _snap_val(snap, "last")
    pairs = [(label, _snap_val(snap, key)) for label, key in keys]
    return _level_items(last, pairs, side)


def _cross_levels(snap: dict | None, kind: str) -> list[dict]:
    last = _snap_val(snap, "last")
    pairs = []
    for row in (snap or {}).get("crosses") or []:
        if row.get("kind") != kind:
            continue
        price = _px(row.get("price"))
        if price is None:
            continue
        pairs.append((row.get("pair") or kind, price))
    return _level_items(last, pairs, "above" if kind == "金叉" else "below")


VOLUME_NOTES = {
    "vol_up", "vol_down", "vol_high", "vol_low",
    "obv_up", "obv_down", "obv_high", "obv_low",
    "price_high_obv_flat", "price_low_obv_flat",
    "mfi_gt80", "mfi_up", "mfi_down", "mfi_cross_down_80", "mfi_cross_up_20",
    "cmf_pos", "cmf_neg", "ad_up", "ad_down",
    "rsi_gt70", "rsi_gt80", "rsi_gt50", "rsi_lt50",
    "rsi_cross_up_30", "rsi_cross_down_70", "rsi_cross_up_50", "rsi_cross_down_50",
    "macd_golden", "macd_death", "macd_pos", "macd_neg", "macd_hist_up", "macd_hist_down",
    "adx_lt15", "adx_15_20", "adx_gt20", "adx_gt25", "adx_gt40", "adx_up",
    "plus_di_lead", "minus_di_lead",
    "stoch_overbought", "stoch_golden", "stoch_death",
    "kdj_golden", "kdj_death", "kdj_above", "kdj_below", "kdj_overbought", "kdj_oversold",
    "cci_gt100", "cci_cross_up_100", "cci_cross_down_m100",
    "roc_pos", "roc_neg", "roc_up",
    "atr_up", "atr_down", "atr_spike", "atr_drop", "atr_pct_high",
    "bb_squeeze", "bb_expand", "keltner_squeeze",
    "rs_spy_20_win", "rs_spy_20_lose", "rs_qqq_20_win", "rs_qqq_20_lose",
    "rs_spy_60_win", "rs_qqq_60_win", "rs_spy_120_win",
}

CLAUSE_PRICE_KEYS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "sma20_above": ("above", [("SMA20", "sma20")]),
    "sma50_above": ("above", [("SMA50", "sma50")]),
    "sma200_above": ("above", [("SMA200", "sma200")]),
    "sma20_gt_sma50": ("above", [("SMA20", "sma20"), ("SMA50", "sma50")]),
    "sma50_gt_sma200": ("above", [("SMA50", "sma50"), ("SMA200", "sma200")]),
    "ema20_above": ("above", [("EMA20", "ema20")]),
    "ema50_above": ("above", [("EMA50", "ema50")]),
    "ema20_up": ("above", [("EMA20", "ema20")]),
    "align_bull": ("above", [("EMA10", "ema10"), ("EMA20", "ema20")]),
    "align_bear": ("below", [("EMA10", "ema10"), ("EMA20", "ema20")]),
    "align_tangle": ("above", [("EMA10", "ema10"), ("EMA20", "ema20")]),
    "ichimoku_above_cloud": ("above", [("云顶", "cloud_top"), ("云底", "cloud_bot")]),
    "ichimoku_below_cloud": ("below", [("云底", "cloud_bot"), ("云顶", "cloud_top")]),
    "ichimoku_tk_golden": ("above", [("转换线", "tenkan"), ("基准线", "kijun")]),
    "ichimoku_tk_death": ("below", [("转换线", "tenkan"), ("基准线", "kijun")]),
    "sar_up": ("above", [("SAR", "sar")]),
    "sar_down": ("below", [("SAR", "sar")]),
    "sar_flip_up": ("above", [("SAR", "sar")]),
    "sar_flip_down": ("below", [("SAR", "sar")]),
    "rsi_lt30": ("above", [("支撑", "support"), ("布林下轨", "bb_lower")]),
    "rsi_lt20": ("above", [("支撑", "support"), ("布林下轨", "bb_lower")]),
    "stoch_oversold": ("above", [("支撑", "support"), ("区间低", "range_low")]),
    "kdj_oversold": ("above", [("支撑", "support"), ("区间低", "range_low")]),
    "cci_lt_m100": ("above", [("支撑", "support"), ("布林下轨", "bb_lower")]),
    "mfi_lt20": ("above", [("支撑", "support"), ("VWAP", "vwap")]),
    "rsi_bull_div": ("above", [("区间低", "range_low"), ("支撑", "support")]),
    "rsi_bear_div": ("below", [("区间高", "range_high"), ("压力", "resistance")]),
    "macd_bull_div": ("above", [("区间低", "range_low"), ("支撑", "support")]),
    "macd_bear_div": ("below", [("区间高", "range_high"), ("压力", "resistance")]),
    "intraday_oversold": ("above", [("支撑", "support"), ("区间低", "range_low")]),
    "week_oversold": ("above", [("支撑", "support"), ("区间低", "range_low")]),
    "bb_up": ("above", [("布林中轨", "bb_middle"), ("布林上轨", "bb_upper")]),
    "bb_down": ("below", [("布林中轨", "bb_middle"), ("布林下轨", "bb_lower")]),
    "bb_above_upper": ("above", [("布林上轨", "bb_upper"), ("布林中轨", "bb_middle")]),
    "bb_below_lower": ("below", [("布林下轨", "bb_lower"), ("布林中轨", "bb_middle")]),
    "sideways": ("above", [("区间高", "range_high"), ("区间低", "range_low")]),
    "keltner_above": ("above", [("Keltner上", "keltner_upper"), ("EMA20", "ema20")]),
    "keltner_below": ("below", [("Keltner下", "keltner_lower"), ("EMA20", "ema20")]),
    "keltner_break_up": ("above", [("Keltner上", "keltner_upper")]),
    "keltner_break_down": ("below", [("Keltner下", "keltner_lower")]),
    "vwap_above": ("above", [("VWAP", "vwap")]),
    "vwap_below": ("below", [("VWAP", "vwap")]),
    "vwap_cross_up": ("above", [("VWAP", "vwap")]),
    "vwap_cross_down": ("below", [("VWAP", "vwap")]),
    "vwap_up": ("above", [("VWAP", "vwap")]),
    "vwap_down": ("below", [("VWAP", "vwap")]),
    "climax_top": ("below", [("区间高", "range_high"), ("压力", "resistance")]),
    "exhaustion": ("above", [("区间低", "range_low"), ("支撑", "support")]),
    "high_5": ("above", [("5日高", "hi5"), ("5日高", "donchian5_high")]),
    "low_5": ("below", [("5日低", "lo5"), ("5日低", "donchian5_low")]),
    "break_resistance": ("above", [("突破档", "break_level"), ("压力", "resistance")]),
    "hold_support": ("above", [("站稳档", "hold_level"), ("支撑", "support")]),
    "donchian5_break_up": ("above", [("5日高", "donchian5_high"), ("5日高", "hi5")]),
    "donchian5_break_down": ("below", [("5日低", "donchian5_low"), ("5日低", "lo5")]),
    "high_20": ("above", [("20日高", "hi20"), ("20日高", "donchian_high")]),
    "high_zone_20": ("above", [("20日高", "zone_high20"), ("20日高", "hi20")]),
    "low_20": ("below", [("20日低", "lo20"), ("20日低", "donchian_low")]),
    "low_zone_20": ("below", [("20日低", "zone_low20"), ("20日低", "lo20")]),
    "high_50": ("above", [("50日高", "hi50")]),
    "low_50": ("below", [("50日低", "lo50")]),
    "high_52w": ("above", [("52周高", "hi52")]),
    "low_52w": ("below", [("52周低", "lo52")]),
    "donchian_break_up": ("above", [("Donchian上", "donchian_high"), ("20日高", "hi20")]),
    "donchian_break_down": ("below", [("Donchian下", "donchian_low"), ("20日低", "lo20")]),
}


def _kdj_note(snapshot: dict | None, cid: str) -> str | None:
    snap = snapshot or {}
    k, d, j = snap.get("stoch_k"), snap.get("stoch_d"), snap.get("kdj_j")
    kp, dp = snap.get("stoch_k_prev"), snap.get("stoch_d_prev")
    if k is None or d is None:
        return "K/D 算不出来"
    bits = [f"K {k} · D {d}"]
    if j is not None:
        bits.append(f"J {j}")
    if cid == "kdj_golden":
        if kp is not None and dp is not None and kp <= dp and k > d:
            bits.append("本根刚上穿")
        elif k > d:
            bits.append("已经过了金叉，但不是这一根刚叉")
        else:
            bits.append("还没金叉")
    elif cid == "kdj_death":
        if kp is not None and dp is not None and kp >= dp and k < d:
            bits.append("本根刚下穿")
        elif k < d:
            bits.append("已经死叉，但不是这一根刚叉")
        else:
            bits.append("还在金叉状态")
    elif cid == "kdj_above":
        bits.append("过了金叉" if k > d else "还没金叉")
    elif cid == "kdj_below":
        bits.append("还没金叉" if k < d else "已经过了金叉")
    elif cid == "kdj_overbought":
        bits.append("J > 100 超买" if j is not None and j > 100 else "J 还没到超买")
    elif cid == "kdj_oversold":
        bits.append("J < 0 超卖" if j is not None and j < 0 else "J 还没到超卖")
    return " · ".join(bits)


def clause_levels(clause: dict, snapshot: dict | None = None) -> tuple[list[dict], str | None]:
    if not snapshot or clause.get("kind") == "strategy":
        return [], None
    cid = str(clause.get("id") or "")
    if cid.startswith("kdj_"):
        return [], _kdj_note(snapshot, cid)
    if cid in {"golden", "death"}:
        return _cross_levels(snapshot, "金叉" if cid == "golden" else "死叉"), None
    mapped = CLAUSE_PRICE_KEYS.get(cid)
    if mapped:
        side, keys = mapped
        if clause.get("not"):
            side = "below" if side == "above" else "above"
        return _levels_from_keys(snapshot, keys, side), None
    if cid in VOLUME_NOTES:
        return [], "用来确认，不决定买点"
    return _levels_from_keys(snapshot, [("EMA20", "ema20"), ("支撑", "support")], "above"), None


def _flatten_buy_levels(explain: dict | None, limit: int = 2) -> list[dict]:
    rows = []
    for group in (explain or {}).get("groups") or []:
        for clause in group.get("clauses") or []:
            for level in clause.get("levels") or []:
                if level.get("action") in {"break", "dip"}:
                    rows.append(level)
            nested = clause.get("nested")
            if nested:
                rows.extend(_flatten_buy_levels(nested, limit=8))
    rows.sort(key=lambda row: abs(row.get("gap") or 0))
    return _dedupe_buys(rows)[:limit]


def _collect_clause_buys(
    explain: dict | None, last, breaks: list[dict], dips: list[dict], trade_side: str = "long"
) -> None:
    for group in (explain or {}).get("groups") or []:
        for clause in group.get("clauses") or []:
            for level in clause.get("levels") or []:
                action = level.get("action")
                if action not in {"break", "dip"}:
                    continue
                item = _buy_item(
                    level.get("price"),
                    level.get("label"),
                    action,
                    f"{clause.get('name') or ''} · {level.get('label')}",
                    last,
                    trade_side,
                )
                if not item:
                    continue
                (breaks if action == "break" else dips).append(item)
            if clause.get("nested"):
                _collect_clause_buys(clause["nested"], last, breaks, dips, trade_side)


def _snapshot_fallbacks(
    snapshot: dict | None, last, trade_side: str = "long"
) -> tuple[list[dict], list[dict]]:
    dips, breaks = [], []
    dip_keys = [
        ("支撑", "support"),
        ("站稳档", "hold_level"),
        ("EMA10", "ema10"),
        ("EMA20", "ema20"),
        ("VWAP", "vwap"),
        ("SMA20", "sma20"),
        ("布林中轨", "bb_middle"),
    ]
    break_keys = [
        ("压力", "resistance"),
        ("突破档", "break_level"),
        ("20日高", "hi20"),
        ("5日高", "hi5"),
        ("布林上轨", "bb_upper"),
    ]
    if last is None:
        return [], []
    short = trade_side == "short"
    for label, key in dip_keys:
        price = _snap_val(snapshot, key)
        if price is None or price > last:
            continue
        dips.append(
            _buy_item(
                price, label, "dip", f"{'反弹' if short else '回踩'} {label}", last, trade_side
            )
        )
    for label, key in break_keys:
        price = _snap_val(snapshot, key)
        if price is None or price <= last:
            continue
        breaks.append(
            _buy_item(
                price, label, "break", f"{'跌破' if short else '突破'} {label}", last, trade_side
            )
        )
    return breaks, dips


def _buy_item(price, label, action, why, last, trade_side: str = "long") -> dict:
    px = _px(price)
    if px is None:
        return {}
    short = trade_side == "short"
    if short:
        roles = {"break": "跌破卖出", "dip": "反弹卖出", "now": "现价可空"}
        default = "卖出"
    else:
        roles = {"break": "突破买入", "dip": "回踩买入", "now": "现价可跟"}
        default = "买入"
    gap = None if last is None else round(px - last, 4)
    return {
        "price": px,
        "label": label,
        "action": action,
        "role": roles.get(action, default),
        "why": why,
        "gap": gap,
    }


def _dedupe_buys(rows: list[dict]) -> list[dict]:
    out = []
    seen: set[float] = set()
    for row in rows:
        price = _px(row.get("price"))
        if price is None:
            continue
        key = round(price, 2)
        if any(abs(key - other) <= max(0.02, abs(other) * 0.003) for other in seen):
            continue
        seen.add(key)
        out.append(row)
    return out


def _target_buys(targets, snapshot: dict | None, last, trade_side: str = "long") -> list[dict]:
    rows = []
    short = trade_side == "short"
    for item in targets or []:
        cid = str(item.get("id") if isinstance(item, dict) else item or "")
        spec = BUY_TARGET_KEYS.get(cid)
        if not spec:
            continue
        label, key = spec
        price = _snap_val(snapshot, key)
        if price is None:
            continue
        why = f"最优卖出 · {label}" if short else f"最优买入 · {label}"
        action = "break" if last is not None and price > last else "dip"
        row = _buy_item(price, label, action, why, last, trade_side)
        if not row:
            continue
        row["role"] = "最优卖出" if short else "最优买入"
        rows.append(row)
    return _dedupe_buys(rows)


def buy_suggestions(
    explain: dict | None,
    snapshot: dict | None = None,
    match: bool | None = None,
    targets=None,
    trade_side: str | None = None,
) -> dict:
    side = "short" if str(trade_side or "long").strip().lower() == "short" else "long"
    short = side == "short"
    last = _snap_val(snapshot, "last")
    preferred = _target_buys(targets, snapshot, last, side)
    breaks: list[dict] = []
    dips: list[dict] = []
    _collect_clause_buys(explain, last, breaks, dips, side)
    extra_breaks, extra_dips = _snapshot_fallbacks(snapshot, last, side)
    from_formula = bool(breaks or dips)
    breaks.extend(extra_breaks)
    dips.extend(extra_dips)
    breaks = _dedupe_buys(sorted(breaks, key=lambda row: abs(row.get("gap") or 0)))
    dips = _dedupe_buys(sorted(dips, key=lambda row: abs(row.get("gap") or 0)))
    if preferred:
        items = list(preferred[:2])
        if len(items) < 2 and breaks:
            items.append(breaks[0])
        names = " / ".join(row["label"] for row in preferred[:2])
        if short:
            summary = f"按你选的最优卖点来：{names}。上面的条件只判断现在能不能卖。"
        else:
            summary = f"按你选的最优买点来：{names}。上面的条件只判断现在能不能买。"
        return {"summary": summary, "items": _dedupe_buys(items)[:2]}
    if match:
        now_why = "条件已到，现价可空；反弹更合适" if short else "条件已到，现价可跟；回踩更合适"
        now = _buy_item(last, "现价", "now", now_why, last, side)
        items = [row for row in [now, *dips[:2]] if row]
        summary = "条件已到。更合适的是反弹卖出，不必追空。" if short else "条件已到。更合适的是回踩买入，不必追高。"
    else:
        items = []
        if breaks:
            items.append(breaks[0])
        if dips:
            items.append(dips[0])
        if len(items) < 2 and len(breaks) > 1:
            items.append(breaks[1])
        if len(items) < 2 and len(dips) > 1:
            items.append(dips[1])
        if short:
            summary = "条件还没齐。可以等跌破再卖，或先看更近的反弹卖点。"
            if not from_formula:
                summary = "这条策略偏确认。卖点按最近压力 / 均线来。"
        else:
            summary = "条件还没齐。可以等突破再买，或先看更近的回踩买点。"
            if not from_formula:
                summary = "这条策略偏确认。买点按最近支撑 / 均线来。"
    items = _dedupe_buys(items)[:2]
    if not items:
        summary = "暂时算不出卖点，K 线数据不够。" if short else "暂时算不出买点，K 线数据不够。"
    return {"summary": summary, "items": items}


def flags_from_snapshot(indicators: dict | None) -> dict[str, bool]:
    ind = indicators or {}
    flags = dict(ind.get("flags") or {})
    align = ind.get("align")
    flags.setdefault("golden", bool(ind.get("golden")))
    flags.setdefault("death", bool(ind.get("death")))
    flags.setdefault("align_bull", align == "多头")
    flags.setdefault("align_bear", align == "空头")
    flags.setdefault("align_tangle", align == "纠缠")
    flags.setdefault("bb_up", bool(ind.get("bb_up")))
    flags.setdefault("bb_down", bool(ind.get("bb_down")))
    flags.setdefault("sideways", bool(ind.get("sideways")))
    flags.setdefault("intraday_oversold", bool((ind.get("intraday_oversold") or {}).get("oversold")))
    flags.setdefault("week_oversold", bool((ind.get("week_oversold") or {}).get("oversold")))
    flags.setdefault("climax_top", bool((ind.get("climax_top") or {}).get("ok")))
    flags.setdefault("exhaustion", bool((ind.get("exhaustion") or {}).get("ok")))
    flags.setdefault("break_resistance", bool(ind.get("break_resistance")))
    flags.setdefault("hold_support", bool(ind.get("hold_support")))
    return {key: bool(flags.get(key)) for key in INDICATOR_MAP}


def _eval_ref(sid: int, flags: dict[str, bool], library: dict | None, stack: set[int]) -> bool:
    library = library or {}
    if sid in stack:
        return False
    nested = library.get(sid)
    if not nested:
        return False
    stack.add(sid)
    try:
        return eval_formula(nested, flags, library, stack)
    finally:
        stack.discard(sid)


def _eval_clause(clause: dict, flags: dict[str, bool], library: dict | None = None, stack: set[int] | None = None) -> bool:
    if clause.get("kind") == "strategy":
        hit = _eval_ref(int(clause["id"]), flags, library, stack or set())
    else:
        hit = bool(flags.get(clause["id"]))
    return (not hit) if clause.get("not") else hit


def _eval_group(group: dict, flags: dict[str, bool], library: dict | None = None, stack: set[int] | None = None) -> bool:
    values = [_eval_clause(item, flags, library, stack) for item in group.get("clauses") or []]
    if not values:
        return False
    return all(values) if group.get("join") == "and" else any(values)


def eval_formula(
    formula: dict | None,
    flags: dict[str, bool],
    library: dict | None = None,
    stack: set[int] | None = None,
) -> bool:
    data = formula or {}
    if str(data.get("kind") or "").lower() == "code":
        return False
    values = [_eval_group(group, flags, library, stack) for group in data.get("groups") or []]
    if not values:
        return False
    return all(values) if data.get("join") == "and" else any(values)


def explain_formula(
    formula: dict | None,
    flags: dict[str, bool],
    library: dict | None = None,
    names: dict | None = None,
    stack: set[int] | None = None,
    snapshot: dict | None = None,
) -> dict:
    data = formula or empty_formula()
    library = library or {}
    stack = set() if stack is None else stack
    groups = []
    for group in data.get("groups") or []:
        clauses = []
        for clause in group.get("clauses") or []:
            nested = None
            if clause.get("kind") == "strategy":
                sid = int(clause["id"])
                nested_formula = library.get(sid)
                if nested_formula and sid not in stack:
                    stack.add(sid)
                    try:
                        nested = explain_formula(nested_formula, flags, library, names, stack, snapshot)
                    finally:
                        stack.discard(sid)
                levels = _flatten_buy_levels(nested)
                note = None if levels else "组合策略，买点来自里面的条件"
            else:
                levels, note = clause_levels(clause, snapshot)
            clauses.append({
                "kind": clause.get("kind") or "indicator",
                "id": clause["id"],
                "not": bool(clause.get("not")),
                "name": clause_text(clause, names),
                "hit": _eval_clause(clause, flags, library, stack),
                "levels": levels,
                "note": note,
                "nested": nested,
            })
        groups.append({
            "join": group.get("join") or "and",
            "hit": _eval_group(group, flags, library, stack),
            "clauses": clauses,
        })
    return {
        "join": data.get("join") or "and",
        "hit": eval_formula(data, flags, library, stack),
        "text": formula_text(data, names),
        "groups": groups,
    }
