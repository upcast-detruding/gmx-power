"""Check GMX's API against Arbitrum chain state.

The staking-power entitlement is computed by GMX's backend; there is no
"staking power" contract. It is however *reconstructible* from chain state,
which is what makes it verifiable rather than merely trusted.

The docs say power counts "both GMX and esGMX in the StakedGmxTracker". This
module checks that independently, by comparing the API's accrual rate against the
tokens actually held by the tracker, and reports a position's vesting headroom.

Chain reads go through `gmx_power.rpc`, which defaults to public endpoints. No API
key is required, and a private endpoint is never printed.

    python -m gmx_power.chain
    python -m gmx_power.chain --address 0x...
"""

from __future__ import annotations

import json
import time
import urllib.request
from decimal import Decimal

from . import rpc

WEI = 10**18

# Arbitrum. Verified live via name()/symbol() - see VERIFICATION.md.
GMX_TOKEN = "0xfc5A1A6EB076a2C7aD06eD22C90d7E710E35ad0a"  # "GMX"
ESGMX_TOKEN = "0xf42Ae1D54fd613C9bb14810b0588FaAa09a426cA"  # "Escrowed GMX"
STAKED_GMX_TRACKER = "0x908C4D94D34924765f1eDc22A1DD098397c59dD4"  # "Staked GMX" / sGMX
# Vester.esToken()==esGMX, pairToken()==feeGmxTracker, claimableToken()==GMX,
# rewardTracker()==stakedGmxTracker - all confirmed on-chain.
GMX_VESTER = "0x199070DDfd1CFb69173aa2F7e20906F26B363004"

SEL_TOTAL_SUPPLY = "0x18160ddd"
SEL_BALANCE_OF = "0x70a08231"
SEL_SYMBOL = "0x95d89b41"
# keccak-derived, validated against the known selectors for balanceOf/transfer/totalSupply.
SEL_DEPOSIT_BALANCES = "0xf5d9d63e"  # depositBalances(address,address)
SEL_STAKED_AMOUNTS = "0x10c1c103"  # stakedAmounts(address)
SEL_MAX_VESTABLE = "0x08f26c76"  # getMaxVestableAmount(address)

POWER_URL = "https://arbitrum.gmxapi.io/v1/staking/power?address=0x" + "0" * 40


def _call(to: str, data: str) -> int:
    return rpc.call("arbitrum", to, data) or 0


def _pad(addr: str) -> str:
    return rpc.pad_address(addr)


def total_supply(token: str) -> int:
    return _call(token, SEL_TOTAL_SUPPLY)


def balance_of(token: str, holder: str) -> int:
    return _call(token, SEL_BALANCE_OF + rpc.pad_address(holder))


def symbol(token: str) -> str:
    """Decode an ABI-encoded string return value: [offset][length][data]."""
    hexdata = rpc.call_raw("arbitrum", token, SEL_SYMBOL)
    if not hexdata:
        return "?"
    raw = bytes.fromhex(hexdata[2:])
    if len(raw) < 64:
        return "?"
    length = int.from_bytes(raw[32:64], "big")
    return raw[64 : 64 + length].decode(errors="replace") or "?"


def network_power() -> tuple[int, float]:
    req = urllib.request.Request(POWER_URL, headers={"User-Agent": "gmx-power-simulator"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return int(json.load(r)["totalNetworkPower"]), time.time()


def measure_accrual_rate(gap_seconds: int = 150) -> Decimal:
    """dP/dt in GMX. Power is wei-seconds, so this is the network's staked balance."""
    p0, t0 = network_power()
    time.sleep(gap_seconds)
    p1, t1 = network_power()
    return Decimal(p1 - p0) / Decimal(str(t1 - t0)) / Decimal(WEI)


def deposit_balances(account: str) -> tuple[int, int, int]:
    """(gmx_deposited, esgmx_deposited, staked_total) in wei, from the tracker."""
    gmx = _call(STAKED_GMX_TRACKER, SEL_DEPOSIT_BALANCES + _pad(account) + _pad(GMX_TOKEN))
    es = _call(STAKED_GMX_TRACKER, SEL_DEPOSIT_BALANCES + _pad(account) + _pad(ESGMX_TOKEN))
    total = _call(STAKED_GMX_TRACKER, SEL_STAKED_AMOUNTS + _pad(account))
    return gmx, es, total


def show_account(account: str) -> int:
    from . import api
    from .model import max_safe_vest

    gmx, es, total = deposit_balances(account)
    sp = api.staking_power(account)
    p = sp.position

    print(f"Account {account}\n")
    print("Composition of the staked balance (from chain state):")
    print(f"  GMX                {Decimal(gmx) / WEI:>14,.4f}")
    print(f"  esGMX              {Decimal(es) / WEI:>14,.4f}")
    print(f"  total staked       {Decimal(total) / WEI:>14,.4f}")

    if total != p.current_staked:
        print(f"\n  MISMATCH: the API reports currentStaked = {Decimal(p.current_staked) / WEI:,.4f}")
        print("  Chain state and the API disagree. Trust neither until reconciled.")
        return 1
    print("\n  Matches the API's currentStaked exactly. Power counts GMX + esGMX.")

    if total == 0:
        return 0
    pct_es = Decimal(es) * 100 / Decimal(total)
    print(f"  esGMX is {pct_es:.2f}% of this position.")

    headroom = p.headroom
    vester_max = _call(GMX_VESTER, SEL_MAX_VESTABLE + _pad(account))
    safe = max_safe_vest(es, p.current_staked, p.peak_staked, vester_max)

    print("\nVesting risk (three caps bind; the smallest wins)")
    print(f"  staked esGMX       {Decimal(es) / WEI:>14,.4f}")
    print(f"  loyalty headroom   {Decimal(headroom) / WEI:>14,.4f}   (floor {Decimal(p.floor) / WEI:,.4f})")
    print(f"  vester max         {Decimal(vester_max) / WEI:>14,.4f}   getMaxVestableAmount()")
    print(f"  -> max safe vest   {Decimal(safe) / WEI:>14,.4f} esGMX")

    binding = "loyalty headroom" if safe == headroom else ("vester limit" if safe == vester_max else "esGMX held")
    print(f"     binding cap:    {binding}")

    if es > headroom:
        excess = Decimal(es - headroom) / WEI
        print(f"\n  Vesting all {Decimal(es) / WEI:,.2f} esGMX exceeds the headroom by {excess:,.2f} GMX.")
        print("  Doing so breaches the 80% floor and resets accrued power to zero.")
        projected = p.projected_gmx()
        if projected:
            print(f"  That would forfeit a claim currently worth {projected:,.2f} GMX.")
        print("\n  Vesting exactly the headroom leaves you *on* the floor with zero")
        print("  headroom, permanently: unstaking never lowers the peak.")
    else:
        print("\n  All staked esGMX can be vested without breaching the floor.")

    print("\n  Unstaking also burns multiplier points (bnGMX) proportionally")
    print("  (RewardRouterV2._unstakeGmx). That cost applies even within the headroom.")
    return 0


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="gmx_power.chain", description=__doc__.splitlines()[0])
    ap.add_argument("--address", help="show one account's GMX/esGMX split and vesting risk")
    args = ap.parse_args()
    if args.address:
        return show_account(args.address)

    print("Identifying contracts on Arbitrum...")
    for label, addr in (("GMX", GMX_TOKEN), ("esGMX", ESGMX_TOKEN), ("tracker", STAKED_GMX_TRACKER)):
        print(f"  {label:9} {addr}  symbol={symbol(addr)!r}")

    gmx_held = Decimal(balance_of(GMX_TOKEN, STAKED_GMX_TRACKER)) / WEI
    es_held = Decimal(balance_of(ESGMX_TOKEN, STAKED_GMX_TRACKER)) / WEI
    sgmx = Decimal(total_supply(STAKED_GMX_TRACKER)) / WEI

    print("\nStaking tracker holdings:")
    print(f"  GMX               {gmx_held:>14,.0f}")
    print(f"  esGMX             {es_held:>14,.0f}")
    print(f"  GMX + esGMX       {gmx_held + es_held:>14,.0f}")
    print(f"  sGMX totalSupply  {sgmx:>14,.0f}")

    print(f"\nMeasuring the API's power accrual rate over {150}s...")
    rate = measure_accrual_rate()
    print(f"  dP/dt = {rate:,.0f} GMX\n")

    print("Which balance does staking power actually count?")
    for label, value in (("GMX only", gmx_held), ("GMX + esGMX", gmx_held + es_held), ("sGMX supply", sgmx)):
        ratio = value / rate if rate else Decimal(0)
        verdict = "MATCH" if Decimal("0.99") < ratio < Decimal("1.01") else "excluded"
        print(f"  {label:14} {value:>12,.0f}   ratio {ratio:.4f}   {verdict}")

    print("\nStaked esGMX counts toward power. Vesting esGMX requires unstaking it,")
    print("which lowers your balance and can breach the 80% loyalty floor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
