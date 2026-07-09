"""Staking-power and loyalty-rule math for the post-2026-03-04 GMX regime.

Pure functions over integers. No network, no floats for money.

Units
-----
GMX amounts are wei (1 GMX = 10**18).
Power is wei-seconds: the time-integral of staked balance.

The docs give the formula (`accumulatedPower += currentBalance x Δt`) but not the
unit the API reports it in. wei-seconds was inferred, then proven by sampling the
live counter: see VERIFICATION.md, and
`tests/test_model.py::test_power_units_are_wei_seconds`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

WEI = 10**18

# Protocol constants, confirmed against the live API (see VERIFICATION.md).
LOYALTY_NUMERATOR = 4  # balance must stay >= 80% of historical peak
LOYALTY_DENOMINATOR = 5
POWER_ACCRUAL_START = 1772582400  # 2026-03-04T00:00:00Z
LOYALTY_TRACKING_START = 1774396800  # 2026-03-25T00:00:00Z


def to_gmx(wei: int) -> Decimal:
    return Decimal(wei) / Decimal(WEI)


def loyalty_floor(peak_staked: int) -> int:
    """Lowest balance that preserves accumulated power. Falling *below* resets."""
    return peak_staked * LOYALTY_NUMERATOR // LOYALTY_DENOMINATOR


def loyalty_ratio(current_staked: int, peak_staked: int) -> Decimal | None:
    if peak_staked <= 0:
        return None
    return Decimal(current_staked) / Decimal(peak_staked)


def max_safe_unstake(current_staked: int, peak_staked: int) -> int:
    """How much can be withdrawn without resetting power.

    Note this is measured against the *peak*, not the current balance. Someone
    sitting exactly at their peak can only ever withdraw 20% of it.
    """
    return max(0, current_staked - loyalty_floor(peak_staked))


def would_reset(new_staked: int, peak_staked: int) -> bool:
    if peak_staked <= 0:
        return False
    return new_staked < loyalty_floor(peak_staked)


@dataclass(frozen=True)
class Position:
    current_staked: int
    peak_staked: int
    cumulative_power: int
    total_network_power: int
    treasury_gmx: int | None
    """None on non-leader chains. Avalanche tracks its own power pool but holds
    no treasury; only Arbitrum reports a balance. How the treasury is split
    across chains at distribution is not documented. See VERIFICATION.md."""

    @property
    def floor(self) -> int:
        return loyalty_floor(self.peak_staked)

    @property
    def headroom(self) -> int:
        return max_safe_unstake(self.current_staked, self.peak_staked)

    @property
    def share(self) -> Decimal:
        """Share of *this chain's* power pool, not of all chains."""
        if self.total_network_power <= 0:
            return Decimal(0)
        return Decimal(self.cumulative_power) / Decimal(self.total_network_power)

    def projected_gmx(self) -> Decimal | None:
        """Share of the treasury as it stands today. Grows as the treasury does.

        None where the chain reports no treasury: we will not invent a figure.
        """
        if self.treasury_gmx is None:
            return None
        return self.share * Decimal(self.treasury_gmx) / Decimal(WEI)

    def projected_value(self, price_usd: Decimal) -> Decimal | None:
        projected = self.projected_gmx()
        return None if projected is None else projected * price_usd


@dataclass(frozen=True)
class Simulation:
    action: str
    amount: int
    new_staked: int
    new_peak: int
    new_floor: int
    new_headroom: int
    resets_power: bool
    power_forfeited: int
    floor_delta: int
    headroom_delta: int


def simulate(position: Position, *, unstake: int = 0, add: int = 0) -> Simulation:
    """Model a stake change against the loyalty rule.

    Adding raises the historical peak the moment the new balance exceeds it,
    which raises the floor with it. That is the counter-intuitive part: topping
    up does not buy proportional withdrawal headroom.
    """
    if unstake and add:
        raise ValueError("simulate one action at a time")
    if unstake < 0 or add < 0:
        raise ValueError("amounts must be non-negative")

    delta = add - unstake
    new_staked = position.current_staked + delta
    if new_staked < 0:
        raise ValueError("cannot unstake more than is staked")

    new_peak = max(position.peak_staked, new_staked)
    resets = would_reset(new_staked, new_peak)
    new_floor = loyalty_floor(new_peak)
    new_headroom = max_safe_unstake(new_staked, new_peak)

    return Simulation(
        action="unstake" if unstake else ("add" if add else "none"),
        amount=unstake or add,
        new_staked=new_staked,
        new_peak=new_peak,
        new_floor=new_floor,
        new_headroom=new_headroom,
        resets_power=resets,
        power_forfeited=position.cumulative_power if resets else 0,
        floor_delta=new_floor - position.floor,
        headroom_delta=new_headroom - position.headroom,
    )


def max_safe_vest(
    esgmx_staked: int,
    current_staked: int,
    peak_staked: int,
    vester_max: int | None = None,
) -> int:
    """Most esGMX that can be vested without resetting power.

    Three independent caps bind, and the smallest wins:

    1. the esGMX actually staked;
    2. the loyalty headroom - `Vester.deposit` pulls esGMX from the wallet, so
       staked esGMX must be unstaked first, and `RewardTracker._unstake`
       decrements `stakedAmounts`, which is what the loyalty rule watches;
    3. `Vester.getMaxVestableAmount(account)`, the protocol's own limit derived
       from cumulative rewards.

    A position that is mostly esGMX therefore cannot be vested out without
    forfeiting its claim on the treasury.
    """
    caps = [esgmx_staked, max_safe_unstake(current_staked, peak_staked)]
    if vester_max is not None:
        caps.append(vester_max)
    return min(caps)


def power_accrued(staked_wei: int, seconds: int) -> int:
    """Power is the integral of balance over time; constant balance is a rectangle."""
    return staked_wei * seconds


def implied_average_staked(total_power: int, elapsed_seconds: int) -> Decimal:
    """Invert the integral to recover mean network stake. Used as a units check."""
    if elapsed_seconds <= 0:
        return Decimal(0)
    return Decimal(total_power) / Decimal(elapsed_seconds) / Decimal(WEI)
