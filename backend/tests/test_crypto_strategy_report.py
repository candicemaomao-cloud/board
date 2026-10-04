from types import SimpleNamespace

from app.services.crypto_strategies import build_hit_report


def test_hit_report_only_lists_actual_hits_and_includes_decision_fields():
    row = SimpleNamespace(symbol="ETH", name="智能波段策略", timeframe="1h")
    result = {
        "price": 2705.55,
        "asof": "2026-10-04 03:00 UTC",
        "prices": {"atr": 42.0, "support": 2648.0, "resistance": 2815.0},
        "combo_details": [
            {"id": "rsi_cross_up_30", "name": "RSI 上穿 30", "hit": False},
            {"id": "macd_golden", "name": "MACD 金叉", "hit": True},
            {"id": "macd_death", "name": "MACD 死叉", "hit": False},
            {"id": "vol_high", "name": "放量", "hit": True},
        ],
    }
    backtest = {
        "bars": 180,
        "trades": 12,
        "win_rate": 0.5833,
        "payoff_ratio": 1.72,
        "range": {"start": "2026-08-01", "end": "2026-10-04"},
    }

    review = {
        "algorithm": "three-window-shape-v1",
        "evidence_id": "abc123def456",
        "horizon": 7,
        "sources": ["BTCUSDT", "ETHUSDT", "OPUSDT"],
        "windows": [
            {"label": "短线", "bars": 7, "direction": "看涨", "up": 66.7, "flat": 8.3, "down": 25.0, "samples": 12, "source_count": 4},
            {"label": "中期", "bars": 14, "direction": "看涨", "up": 58.3, "flat": 8.3, "down": 33.4, "samples": 12, "source_count": 3},
            {"label": "长线", "bars": 30, "direction": "震荡", "up": 41.7, "flat": 25.0, "down": 33.3, "samples": 12, "source_count": 3},
        ],
        "consensus": {"direction": "看涨", "agreement": 2, "score": 61, "ready": True},
        "forecast": {
            "horizon_bars": 7, "sample_count": 24, "window_count": 2,
            "upside_probe_pct": {"low": 2.1, "median": 3.2, "high": 5.4},
            "downside_probe_pct": {"low": -3.5, "median": -2.1, "high": -0.8},
            "final_return_pct": {"low": 1.2, "median": 2.8, "high": 4.7},
            "duration_bars": {"low": 2, "median": 3, "high": 5},
        },
    }
    report = build_hit_report(row, result, backtest, review)

    assert "结论：指标与三阶段历史共振均偏多" in report
    assert "✓ MACD 金叉" in report
    assert "短期动能转强" in report
    assert "✓ 放量" in report
    assert "市场参与度升高" in report
    assert "✓ RSI 上穿 30" not in report
    assert "✓ MACD 死叉" not in report
    assert "未命中：2 项配置条件" in report
    assert "观察买入区：" in report
    assert "失效 / 止损：" in report
    assert "胜率：58.3%" in report
    assert "短 / 中 / 长三阶段形态复核" in report
    assert "短线 7根：看涨" in report
    assert "12例 / 4个币种源" in report
    assert "2/3 同向" in report
    assert "证据：three-window-shape-v1 · abc123def456" in report
    assert "后续路径区间" in report
    assert "上探幅度：+2.10% ～ +5.40%" in report
    assert "下探幅度：-3.50% ～ -0.80%" in report
    assert "第 7 根收盘：+1.20% ～ +4.70%" in report
    assert "常见方向持续：第 2 ～ 5 根" in report
    assert "【虚拟币综合策略报告】" in report
    assert "同一事件只推送一次" in report


def test_hit_report_does_not_present_bearish_signal_as_spot_buy():
    row = SimpleNamespace(symbol="ETH", name="风险监控", timeframe="1h")
    result = {
        "price": 2705.55,
        "asof": "2026-10-04 03:00 UTC",
        "prices": {"atr": 42.0, "support": 2648.0, "resistance": 2815.0},
        "combo_details": [
            {"id": "macd_death", "name": "MACD 死叉", "hit": True},
            {"id": "vol_high", "name": "放量", "hit": True},
        ],
    }

    report = build_hit_report(row, result)

    assert "方向：偏空" in report
    assert "仅监控预警" in report
    assert "有效交易样本不足" in report
    assert "【虚拟币指标监控预警】" in report
    assert "复核数据获取失败" in report


def test_all_requested_indicator_hits_have_plain_language_explanations():
    row = SimpleNamespace(symbol="OP", name="解释测试", timeframe="1h")
    result = {
        "price": 0.13,
        "asof": "2026-10-04 04:00 UTC",
        "prices": {"atr": 0.01, "support": 0.12, "resistance": 0.15},
        "combo_details": [
            {"id": "rsi_cross_up_30", "name": "RSI 上穿 30", "hit": True},
            {"id": "rsi_cross_down_70", "name": "RSI 下穿 70", "hit": True},
            {"id": "macd_golden", "name": "MACD 金叉", "hit": True},
            {"id": "macd_death", "name": "MACD 死叉", "hit": True},
            {"id": "atr_spike", "name": "ATR 突增", "hit": True},
            {"id": "vol_high", "name": "放量", "hit": True},
        ],
    }

    report = build_hit_report(row, result)

    for phrase in ("反弹拐点", "回撤风险", "短期动能转强", "短期动能转弱", "不判断涨跌方向", "市场参与度升高"):
        assert phrase in report
