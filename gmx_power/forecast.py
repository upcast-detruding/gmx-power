"""Buyback timeline, treasury projection, and time-to-$90 arithmetic.

**This module cannot predict when GMX reaches $90, and does not try.**

Buyback accrual is observable and can be extrapolated with stated assumptions.
Price cannot. Buybacks add demand, but the map from demand to price is not
something this data determines, and anyone quoting you a date has substituted a
guess for it. So `months_to_target` takes the growth rate *you* assume and does
the arithmetic. It is a calculator, not a forecast: change the assumption and the
answer changes, which is the point.

What is genuinely projectable is the treasury: it grows at the observed accrual
rate, which has been falling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from . import api

TARGET_PRICE = Decimal(90)
WEEKS_PER_MONTH = Decimal("4.348")


@dataclass(frozen=True)
class WeekPoint:
    start: int
    gmx: Decimal
    cumulative: Decimal

    @property
    def date(self) -> str:
        return datetime.fromtimestamp(self.start, timezone.utc).strftime("%Y-%m-%d")


def timeline(chain: str = "arbitrum") -> list[WeekPoint]:
    """Dated weekly buyback accrual. Complete weeks only.

    The in-progress week reads near zero and would misrepresent both the latest
    figure and any mean taken over it.
    """
    bb = api.buyback_stats(chain)
    points, running = [], Decimal(0)
    for week in bb.complete_weeks():
        running += week.gmx
        points.append(WeekPoint(start=week.start, gmx=week.gmx, cumulative=running))
    return points


def accrual_rates(points: list[WeekPoint]) -> dict[str, Decimal]:
    """Weekly GMX accrual under three readings of the same history."""
    if not points:
        return {}
    active = [p.gmx for p in points if p.gmx > 0]
    last4 = [p.gmx for p in points[-4:]]
    return {
        "latest complete week": points[-1].gmx,
        "mean of last 4 weeks": sum(last4) / Decimal(len(last4)),
        "mean of active weeks": (sum(active) / Decimal(len(active))) if active else Decimal(0),
    }


def trend_pct(points: list[WeekPoint], n: int = 5) -> Decimal | None:
    """Percentage change across the last n complete weeks."""
    window = [p.gmx for p in points[-n:]]
    if len(window) < 2 or window[0] == 0:
        return None
    return (window[-1] - window[0]) / window[0] * 100


def project_treasury(treasury_now: Decimal, weekly_gmx: Decimal, weeks: int) -> Decimal:
    """Treasury in GMX after `weeks`, assuming accrual holds. It has not been holding."""
    return treasury_now + weekly_gmx * Decimal(weeks)


def required_total_return(price: Decimal, target: Decimal = TARGET_PRICE) -> Decimal:
    """Multiple of today's price needed to reach the target."""
    return target / price


def months_to_target(
    price: Decimal, monthly_return: Decimal, target: Decimal = TARGET_PRICE
) -> Decimal | None:
    """Months of compounding at `monthly_return` to go from price to target.

    Returns None if the target is unreachable under the assumption (no growth, or
    the price is already there). This is arithmetic on *your* assumption. The data
    in this package says nothing about whether that assumption is reasonable.
    """
    if price <= 0 or monthly_return <= 0 or price >= target:
        return None
    return Decimal(str(math.log(float(target / price)) / math.log(float(1 + monthly_return))))


def scenario_table(
    price: Decimal, rates: tuple[str, ...] = ("0.01", "0.02", "0.03", "0.05", "0.10")
) -> list[tuple[Decimal, Decimal | None]]:
    """(monthly return, months to $90) for a spread of assumed growth rates."""
    out = []
    for r in rates:
        rate = Decimal(r)
        out.append((rate, months_to_target(price, rate)))
    return out
