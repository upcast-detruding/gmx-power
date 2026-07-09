"""Math tests. No network: live values are pinned as constants."""

from decimal import Decimal

import pytest

from gmx_power.model import (
    LOYALTY_TRACKING_START,
    POWER_ACCRUAL_START,
    WEI,
    Position,
    implied_average_staked,
    loyalty_floor,
    max_safe_unstake,
    max_safe_vest,
    simulate,
    would_reset,
)

# Observed live at the Arbitrum API on 2026-07-09 (see VERIFICATION.md).
LIVE_TOTAL_NETWORK_POWER = 78074310731819743236260333998306
LIVE_TREASURY_GMX = 334390900000000000000000
NOW_2026_07_09 = 1783555200  # 2026-07-09T00:00:00Z


def gmx(n: str | int) -> int:
    return int(Decimal(str(n)) * WEI)


class TestProtocolConstants:
    def test_accrual_start_is_2026_03_04(self):
        # 2026-03-04T00:00:00Z, matching the governance announcement.
        assert POWER_ACCRUAL_START == 1772582400

    def test_loyalty_tracking_starts_three_weeks_later(self):
        assert LOYALTY_TRACKING_START == 1774396800
        weeks = (LOYALTY_TRACKING_START - POWER_ACCRUAL_START) / (7 * 86400)
        assert weeks == 3.0


class TestPowerUnits:
    def test_power_units_are_wei_seconds(self):
        """Invert the integral: mean network stake must be a sane GMX figure.

        If power were wei-days the implied stake would be ~614bn GMX, which is
        impossible against a ~10.4M total supply. wei-seconds gives ~7.1M GMX
        staked, i.e. ~68% of circulating supply. That is the unit.
        """
        elapsed = NOW_2026_07_09 - POWER_ACCRUAL_START
        assert elapsed == 10972800  # 127 days

        mean_staked = implied_average_staked(LIVE_TOTAL_NETWORK_POWER, elapsed)
        assert Decimal("6_500_000") < mean_staked < Decimal("7_500_000")

        # The wei-days reading is absurd, and stays rejected.
        as_days = implied_average_staked(LIVE_TOTAL_NETWORK_POWER, elapsed // 86400)
        assert as_days > Decimal("10_000_000_000")


class TestLoyaltyFloor:
    def test_floor_is_eighty_percent_of_peak(self):
        assert loyalty_floor(gmx(1000)) == gmx(800)

    def test_floor_of_zero_peak_is_zero(self):
        assert loyalty_floor(0) == 0

    def test_floor_rounds_down_never_trapping_the_staker(self):
        # 3 wei * 4 // 5 == 2, i.e. floor <= exact 80%. Rounding must not push
        # the floor above the true threshold, which would falsely trigger resets.
        assert loyalty_floor(3) == 2
        assert loyalty_floor(3) <= 3 * 4 / 5


class TestResetBoundary:
    peak = gmx(1000)

    def test_exactly_at_floor_is_safe(self):
        assert not would_reset(gmx(800), self.peak)

    def test_one_wei_below_floor_resets(self):
        assert would_reset(gmx(800) - 1, self.peak)

    def test_above_floor_is_safe(self):
        assert not would_reset(gmx(801), self.peak)

    def test_never_staked_cannot_reset(self):
        assert not would_reset(0, 0)


class TestHeadroom:
    def test_at_peak_headroom_is_twenty_percent(self):
        assert max_safe_unstake(gmx(1000), gmx(1000)) == gmx(200)

    def test_below_peak_headroom_shrinks(self):
        # Already down to 900 against a 1000 peak: only 100 left, not 180.
        assert max_safe_unstake(gmx(900), gmx(1000)) == gmx(100)

    def test_below_floor_headroom_is_zero_not_negative(self):
        assert max_safe_unstake(gmx(700), gmx(1000)) == 0


def position(current, peak, power=10**30) -> Position:
    return Position(
        current_staked=current,
        peak_staked=peak,
        cumulative_power=power,
        total_network_power=LIVE_TOTAL_NETWORK_POWER,
        treasury_gmx=LIVE_TREASURY_GMX,
    )


class TestSimulateUnstake:
    def test_safe_unstake_preserves_power(self):
        s = simulate(position(gmx(1000), gmx(1000)), unstake=gmx(200))
        assert not s.resets_power
        assert s.power_forfeited == 0
        assert s.new_staked == gmx(800)

    def test_one_wei_too_far_forfeits_everything(self):
        p = position(gmx(1000), gmx(1000))
        s = simulate(p, unstake=gmx(200) + 1)
        assert s.resets_power
        assert s.power_forfeited == p.cumulative_power

    def test_unstaking_does_not_lower_the_peak(self):
        s = simulate(position(gmx(1000), gmx(1000)), unstake=gmx(200))
        assert s.new_peak == gmx(1000)
        # So headroom is now zero: you cannot repeat the trick.
        assert s.new_headroom == 0

    def test_cannot_unstake_more_than_staked(self):
        with pytest.raises(ValueError):
            simulate(position(gmx(100), gmx(100)), unstake=gmx(101))


class TestSimulateAdd:
    def test_adding_raises_the_peak_and_the_floor(self):
        p = position(gmx(1000), gmx(1000))
        s = simulate(p, add=gmx(1000))
        assert s.new_peak == gmx(2000)
        assert s.new_floor == gmx(1600)
        assert s.floor_delta == gmx(800)

    def test_adding_buys_only_twenty_percent_headroom(self):
        """The counter-intuitive result worth publishing.

        Add 1000 GMX at your peak and you can withdraw only 200 more, not 1000.
        """
        p = position(gmx(1000), gmx(1000))
        s = simulate(p, add=gmx(1000))
        assert s.headroom_delta == gmx(200)

    def test_adding_below_peak_does_not_move_the_floor(self):
        # Recovering toward an old peak restores headroom without raising it.
        p = position(gmx(850), gmx(1000))
        s = simulate(p, add=gmx(50))
        assert s.new_peak == gmx(1000)
        assert s.floor_delta == 0
        assert s.headroom_delta == gmx(50)

    def test_adding_never_resets(self):
        s = simulate(position(gmx(900), gmx(1000)), add=gmx(1))
        assert not s.resets_power


class TestEntitlement:
    def test_share_is_power_over_network_power(self):
        p = position(gmx(1000), gmx(1000), power=LIVE_TOTAL_NETWORK_POWER // 100)
        assert p.share == Decimal("0.01")

    def test_projected_claim_scales_with_treasury(self):
        p = position(gmx(1000), gmx(1000), power=LIVE_TOTAL_NETWORK_POWER // 100)
        assert p.projected_gmx() == Decimal(LIVE_TREASURY_GMX) / Decimal(WEI) / 100

    def test_zero_network_power_does_not_divide_by_zero(self):
        p = Position(gmx(1), gmx(1), 0, 0, 0)
        assert p.share == Decimal(0)
        assert p.projected_gmx() == Decimal(0)


class TestVesting:
    """Vesting esGMX unstakes it, so the loyalty headroom caps how much can be vested."""

    def test_vest_is_capped_by_headroom_not_by_holding(self):
        # An all-esGMX position sitting at its peak: only 20% may leave.
        assert max_safe_vest(gmx(10000), gmx(10000), gmx(10000)) == gmx(2000)

    def test_small_esgmx_holding_is_fully_vestable(self):
        assert max_safe_vest(gmx(100), gmx(10000), gmx(10000)) == gmx(100)

    def test_no_headroom_means_no_safe_vest(self):
        # Already at the floor after an earlier withdrawal.
        assert max_safe_vest(gmx(1000), gmx(800), gmx(1000)) == 0

    def test_pure_esgmx_position_cannot_be_fully_vested(self):
        staked = gmx(1000)
        assert max_safe_vest(staked, staked, staked) < staked

    def test_vester_limit_binds_when_it_is_the_smallest(self):
        # getMaxVestableAmount can be below both the holding and the headroom.
        assert max_safe_vest(gmx(1000), gmx(5000), gmx(5000), vester_max=gmx(50)) == gmx(50)

    def test_headroom_still_binds_when_vester_limit_is_larger(self):
        # An esGMX-heavy position: most of it vestable in principle, but the
        # loyalty headroom caps it at 20% of the peak.
        assert max_safe_vest(
            gmx(9990), gmx(10000), gmx(10000), vester_max=gmx(8500)
        ) == gmx(2000)

    def test_omitting_vester_max_ignores_that_cap(self):
        assert max_safe_vest(gmx(100), gmx(5000), gmx(5000)) == gmx(100)


class TestNonLeaderChain:
    """Avalanche reports totalNetworkPower but treasuryGmxBalance: null."""

    def test_no_treasury_yields_no_projection_rather_than_zero(self):
        p = Position(gmx(100), gmx(100), 10**30, LIVE_TOTAL_NETWORK_POWER, None)
        assert p.projected_gmx() is None
        assert p.projected_value(Decimal(90)) is None

    def test_share_still_computable_without_a_treasury(self):
        p = Position(gmx(100), gmx(100), LIVE_TOTAL_NETWORK_POWER // 4, LIVE_TOTAL_NETWORK_POWER, None)
        assert p.share == Decimal("0.25")

    def test_loyalty_rule_is_unaffected_by_missing_treasury(self):
        p = Position(gmx(1000), gmx(1000), 10**30, LIVE_TOTAL_NETWORK_POWER, None)
        assert simulate(p, unstake=gmx(200) + 1).resets_power


class TestArgumentGuards:
    def test_cannot_add_and_unstake_at_once(self):
        with pytest.raises(ValueError):
            simulate(position(gmx(1), gmx(1)), unstake=1, add=1)

    def test_negative_amounts_rejected(self):
        with pytest.raises(ValueError):
            simulate(position(gmx(1), gmx(1)), unstake=-1)
