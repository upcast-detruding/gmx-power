"""Time-to-$90 is arithmetic on an assumption, and must behave like one."""

from decimal import Decimal

from gmx_power.forecast import (
    TARGET_PRICE,
    WeekPoint,
    accrual_rates,
    months_to_target,
    project_treasury,
    required_total_return,
    scenario_table,
    trend_pct,
)

WEEK = 7 * 86400


def points(values: list[int], start: int = 1780000000) -> list[WeekPoint]:
    out, run = [], Decimal(0)
    for i, v in enumerate(values):
        run += Decimal(v)
        out.append(WeekPoint(start=start + i * WEEK, gmx=Decimal(v), cumulative=run))
    return out


class TestMonthsToTarget:
    def test_zero_growth_never_arrives(self):
        assert months_to_target(Decimal(6), Decimal(0)) is None

    def test_negative_growth_never_arrives(self):
        assert months_to_target(Decimal(6), Decimal("-0.05")) is None

    def test_already_at_target_returns_none(self):
        assert months_to_target(Decimal(90), Decimal("0.05")) is None
        assert months_to_target(Decimal(100), Decimal("0.05")) is None

    def test_doubling_monthly_from_45_takes_one_month(self):
        m = months_to_target(Decimal(45), Decimal(1))
        assert abs(float(m) - 1.0) < 1e-9

    def test_slower_growth_takes_longer(self):
        fast = months_to_target(Decimal(6), Decimal("0.10"))
        slow = months_to_target(Decimal(6), Decimal("0.01"))
        assert slow > fast

    def test_compounding_is_logarithmic_not_linear(self):
        # 10x the rate is far less than 1/10th the time.
        fast = months_to_target(Decimal(6), Decimal("0.10"))
        slow = months_to_target(Decimal(6), Decimal("0.01"))
        assert slow < fast * 10


class TestRequiredReturn:
    def test_multiple_from_current_price(self):
        assert required_total_return(Decimal(9)) == Decimal(10)

    def test_target_is_ninety(self):
        assert TARGET_PRICE == 90


class TestScenarioTable:
    def test_every_positive_rate_yields_a_number(self):
        rows = scenario_table(Decimal(6))
        assert len(rows) == 5
        assert all(months is not None and months > 0 for _, months in rows)

    def test_rows_are_monotonic_in_rate(self):
        rows = scenario_table(Decimal(6))
        months = [m for _, m in rows]
        assert months == sorted(months, reverse=True)

    def test_price_at_target_yields_no_rows_with_values(self):
        rows = scenario_table(Decimal(90))
        assert all(m is None for _, m in rows)


class TestAccrualAndTrend:
    series = points([100, 200, 0, 300, 400])

    def test_latest_week_is_the_last_complete_one(self):
        assert accrual_rates(self.series)["latest complete week"] == Decimal(400)

    def test_mean_of_last_four(self):
        assert accrual_rates(self.series)["mean of last 4 weeks"] == Decimal(225)

    def test_active_mean_excludes_zero_weeks(self):
        assert accrual_rates(self.series)["mean of active weeks"] == Decimal(250)

    def test_empty_history_yields_no_rates(self):
        assert accrual_rates([]) == {}

    def test_trend_is_a_percentage_change_across_the_window(self):
        assert trend_pct(points([100, 50])) == Decimal(-50)

    def test_declining_trend_is_negative(self):
        assert trend_pct(points([33820, 29460, 25630, 23280, 20740])) < 0


class TestTreasuryProjection:
    def test_projection_is_linear_in_weeks(self):
        assert project_treasury(Decimal(1000), Decimal(10), 52) == Decimal(1520)

    def test_zero_accrual_leaves_treasury_flat(self):
        assert project_treasury(Decimal(1000), Decimal(0), 52) == Decimal(1000)
