"""QQQ vs TLT：只改参数、不写拉数。要自己洗数据请看 _template.py 的 run(ctx)。"""

STRATEGY = {
    "name": "QQQ vs TLT",
    "notes": "实际利率上行时成长股相对国债承压；z 偏离过大做回归。",
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
    "macro_filter": False,
    "beta": None,
}
