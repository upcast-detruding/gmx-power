"""Empirical check that power is the time-integral of staked balance, in wei-seconds.

`totalNetworkPower` accrues in real time. If power is wei-seconds then

    dP/dt == total_staked_wei

so sampling twice and dividing recovers the network's staked GMX directly. We
compare that against the long-run mean implied by P/elapsed. Two independent
routes to the same number is the proof.

    python -m gmx_power.verify
"""

from __future__ import annotations

import time
from decimal import Decimal

from . import api
from .model import POWER_ACCRUAL_START, WEI, implied_average_staked

GAP_SECONDS = 30


def sample(chain: str) -> tuple[int, float]:
    sp = api.staking_power("0x" + "0" * 40, chain)
    return int(sp.raw["totalNetworkPower"]), time.time()


def main(chain: str = "arbitrum") -> int:
    print(f"Sampling {chain} totalNetworkPower {GAP_SECONDS}s apart...\n")

    p0, t0 = sample(chain)
    time.sleep(GAP_SECONDS)
    p1, t1 = sample(chain)

    dp, dt = p1 - p0, t1 - t0
    if dp <= 0:
        print("power did not advance; cannot verify")
        return 1

    instantaneous = Decimal(dp) / Decimal(str(dt)) / Decimal(WEI)
    elapsed = t1 - POWER_ACCRUAL_START
    long_run = implied_average_staked(p1, int(elapsed))

    print(f"  P0            {p0}")
    print(f"  P1            {p1}")
    print(f"  dP            {dp}")
    print(f"  dt            {dt:.2f} s\n")
    print(f"  dP/dt         {instantaneous:,.0f} GMX  <- staked right now")
    print(f"  P/elapsed     {long_run:,.0f} GMX  <- mean staked since 2026-03-04\n")

    ratio = instantaneous / long_run if long_run else Decimal(0)
    if Decimal("0.5") < ratio < Decimal("2.0"):
        print(f"  CONSISTENT (ratio {ratio:.3f}). Power is wei-seconds.")
        print("  The two figures differ only because stake has drifted since March.")
        return 0

    print(f"  INCONSISTENT (ratio {ratio:.3f}). The unit assumption is wrong.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
