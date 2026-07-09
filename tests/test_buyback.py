"""The in-progress week must never be treated as a completed zero-accrual week."""

from decimal import Decimal

from gmx_power.api import BuybackStats

WEEK = 7 * 86400
NOW = 1783600000  # mid-week, 2026-07-09


def raw(weeks: list[tuple[int, int]]) -> dict:
    return {
        "summary": {
            "totalAccrued": str(sum(a for _, a in weeks)),
            "latestWeekAccrued": str(weeks[-1][1]) if weeks else "0",
            "weeksTracked": len(weeks),
        },
        "weeks": [
            {
                "weekStart": s,
                "weekEnd": s + WEEK,
                "weeklyAccrued": str(a),
                "cumulativeAccrued": "0",
            }
            for s, a in weeks
        ],
    }


GMX = 10**18
# Three complete weeks, then the current one still running (reads 0 so far).
SERIES = raw([
    (1781654400, 30 * GMX),
    (1782259200, 20 * GMX),
    (1782864000, 10 * GMX),
    (1783468800, 0),  # in progress at NOW
])


class TestPartialWeek:
    def test_current_week_is_not_complete(self):
        weeks = BuybackStats(SERIES).weeks
        assert weeks[-1].is_complete(NOW) is False
        assert all(w.is_complete(NOW) for w in weeks[:-1])

    def test_api_truncates_weekEnd_to_now_for_the_live_week(self):
        """The real API reports weekEnd == now for the in-progress week.

        Testing `end <= now` therefore marks it complete and reports a phantom
        zero-accrual week. Completeness must be derived from weekStart.
        """
        live = raw([(1781654400, 30 * GMX), (1783468800, 0)])
        live["weeks"][-1]["weekEnd"] = int(NOW)  # what the API actually returns
        weeks = BuybackStats(live).weeks
        assert weeks[-1].end <= NOW  # the trap
        assert weeks[-1].is_complete(NOW) is False  # not fooled by it

    def test_complete_weeks_excludes_the_partial_one(self):
        assert len(BuybackStats(SERIES).complete_weeks(NOW)) == 3

    def test_mean_ignores_the_partial_week(self):
        # (30+20+10)/3 == 20, not (30+20+10+0)/4 == 15.
        assert BuybackStats(SERIES).mean_weekly_gmx(now=NOW) == Decimal(20)

    def test_partial_week_does_not_masquerade_as_a_zero_week(self):
        zeros = [w for w in BuybackStats(SERIES).complete_weeks(NOW) if w.gmx == 0]
        assert zeros == []


class TestTrend:
    def test_detects_monotonic_decline(self):
        assert "declining" in BuybackStats(SERIES).trend(now=NOW)

    def test_partial_week_cannot_fake_a_decline(self):
        flat = raw([(1781654400, 10 * GMX), (1782259200, 10 * GMX),
                    (1782864000, 10 * GMX), (1783468800, 0)])
        assert BuybackStats(flat).trend(now=NOW) == "mixed"

    def test_detects_rise(self):
        up = raw([(1781654400, 10 * GMX), (1782259200, 20 * GMX),
                  (1782864000, 30 * GMX), (1783468800, 0)])
        assert "rising" in BuybackStats(up).trend(now=NOW)

    def test_too_few_weeks_is_reported_not_guessed(self):
        assert BuybackStats(raw([(1782864000, 10 * GMX)])).trend(now=NOW) == "insufficient data"
