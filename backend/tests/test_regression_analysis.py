import math
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from app.services.screener import _fit, _period_comparison, _similar_history, analyze_regression


class RegressionAnalysisTests(unittest.TestCase):
    def test_robust_fit_returns_aligned_channels_inputs(self):
        values = [100 + i * 0.5 + math.sin(i / 4) for i in range(80)]
        values[40] += 25
        result = _fit(values)
        self.assertEqual(len(result["fitted"]), len(values))
        self.assertEqual(len(result["deviations"]), len(values))
        self.assertEqual(len(result["event_flags"]), len(values))
        self.assertGreater(result["sigma"], 0)

    def test_period_comparison_marks_unavailable_period(self):
        result = _period_comparison(
            [100 + i for i in range(80)], [60, 120], exclude_events=False, log_price=False
        )
        self.assertTrue(result[0]["available"])
        self.assertFalse(result[1]["available"])

    def test_similar_history_is_walk_forward(self):
        values = [math.log(100 + i * 0.2 + math.sin(i / 5)) for i in range(320)]
        current = _fit(values[-120:])
        result = _similar_history(
            values, current["deviations"][-1], current["slope"],
            exclude_events=False, log_price=True,
        )
        self.assertTrue(result["walk_forward"])
        self.assertEqual(result["horizon"], 20)

    @patch("app.services.screener._annual_series", return_value=None)
    @patch("app.services.screener.fetch_closes_covering")
    def test_analysis_returns_regression_page_sections(self, fetch_closes, _annual):
        first = date(2025, 1, 1)
        bars = []
        for i in range(430):
            day = first + timedelta(days=i)
            close = 100 + i * 0.08 + math.sin(i / 9)
            bars.append({
                "ts": int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()),
                "close": close,
            })
        fetch_closes.return_value = {"ohlc_bars": bars}

        result = analyze_regression(
            "TEST", start=date(2025, 8, 1), end=date(2026, 2, 28),
            timeframe="daily", price_mode="price",
        )
        count = len(result["dates"])
        self.assertEqual(len(result["channel_1_low"]), count)
        self.assertEqual(len(result["channel_2_high"]), count)
        self.assertEqual(len(result["deviation_history"]), count)
        self.assertEqual(len(result["rolling_slope"]), count)
        self.assertEqual([row["period"] for row in result["period_comparison"]], [60, 120, 250])
        self.assertIn("similar_history", result)
        self.assertFalse(result["exclude_events"])


if __name__ == "__main__":
    unittest.main()
