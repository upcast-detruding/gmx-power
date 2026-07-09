"""CLI: inspect a staking position and simulate stake changes.

Read-only. Never signs, never sends a transaction, never asks for a key.

    python -m gmx_power --address 0x... --unstake 500
    python -m gmx_power --address 0x... --add 1000
    python -m gmx_power --buyback
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from decimal import Decimal

from . import api
from .model import WEI, Position, simulate, to_gmx


def _gmx(x: Decimal | int, places: str = "0.0001") -> str:
    d = to_gmx(x) if isinstance(x, int) else x
    return f"{d.quantize(Decimal(places)):,}"


def _ts(epoch: int | None) -> str:
    if not epoch:
        return "never"
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d")


def show_position(sp: api.StakingPower, price: Decimal | None) -> Position:
    p = sp.position
    print("Position")
    print(f"  staked now          {_gmx(p.current_staked)} GMX")
    print(f"  historical peak     {_gmx(p.peak_staked)} GMX")
    print(f"  loyalty floor (80%) {_gmx(p.floor)} GMX")
    print(f"  safe to unstake     {_gmx(p.headroom)} GMX")
    if p.peak_staked:
        ratio = Decimal(p.current_staked) / Decimal(p.peak_staked)
        flag = "OK" if ratio >= Decimal("0.8") else "BELOW FLOOR"
        print(f"  loyalty ratio       {ratio:.4f}  [{flag}]")
    if sp.reset_count:
        print(f"  power resets        {sp.reset_count} (last {_ts(sp.last_reset_at)})")
    if p.peak_staked:
        print("\n  Balance counts staked GMX *and* staked esGMX (verified against chain\n"
              "  state; see VERIFICATION.md). Vesting esGMX unstakes it, lowering this balance.\n"
              "  Vesting more than your headroom will reset your power.")

    print("\nEntitlement")
    print(f"  your power          {p.cumulative_power:,} wei-seconds")
    print(f"  network power       {p.total_network_power:,} wei-seconds  (this chain only)")
    print(f"  your share          {p.share * 100:.6f}%")

    if p.treasury_gmx is None:
        print("  treasury            not reported on this chain")
        print("\n  This chain runs its own power pool but holds no treasury balance.")
        print("  Only the leader chain (Arbitrum) reports one. How the treasury is")
        print("  divided between chains at distribution is not documented anywhere.")
        return p

    print(f"  treasury            {_gmx(p.treasury_gmx, '0.01')} GMX")
    projected = p.projected_gmx()
    print(f"  projected claim     {projected.quantize(Decimal('0.0001')):,} GMX")

    # Cross-check our arithmetic against the API's own figure.
    api_projected = sp.api_projected_gmx
    if api_projected is not None:
        delta = abs(projected - api_projected)
        verdict = "agrees" if delta <= Decimal("0.0001") else f"DISAGREES by {delta}"
        print(f"  api cross-check     {verdict} (api says {api_projected:.4f} GMX)")

    if price:
        print(f"  value at ${price}      ${(projected * price).quantize(Decimal('0.01')):,}")
        print(f"  value at $90         ${(projected * 90).quantize(Decimal('0.01')):,}")
    return p


def show_simulation(p: Position, *, unstake: int, add: int) -> None:
    sim = simulate(p, unstake=unstake, add=add)
    verb = f"{sim.action} {_gmx(sim.amount)} GMX"
    print(f"\nSimulation: {verb}")
    print(f"  staked      {_gmx(p.current_staked)} -> {_gmx(sim.new_staked)} GMX")
    print(f"  peak        {_gmx(p.peak_staked)} -> {_gmx(sim.new_peak)} GMX")
    print(f"  floor       {_gmx(p.floor)} -> {_gmx(sim.new_floor)} GMX  ({sim.floor_delta / WEI:+,.4f})")
    print(f"  headroom    {_gmx(p.headroom)} -> {_gmx(sim.new_headroom)} GMX  ({sim.headroom_delta / WEI:+,.4f})")

    if sim.resets_power:
        print("\n  *** THIS RESETS YOUR POWER TO ZERO ***")
        projected = p.projected_gmx()
        if sim.power_forfeited:
            print(f"  You forfeit {sim.power_forfeited:,} wei-seconds of accrued power,")
            if projected is not None:
                print(f"  currently worth ~{projected.quantize(Decimal('0.0001'))} GMX of the treasury,")
            print("  redistributed to the remaining stakers. It cannot be recovered.")
        else:
            print("  Any accrued power would be forfeited and redistributed to the")
            print("  remaining stakers. It cannot be recovered.")
        print(f"  Maximum you can unstake safely: {_gmx(p.headroom)} GMX")
    elif sim.action == "add":
        print("\n  Power is preserved. Note the floor rose with your new peak:")
        print(f"  adding {_gmx(sim.amount)} GMX bought only "
              f"{sim.headroom_delta / WEI:+,.4f} GMX of extra withdrawal headroom.")
    else:
        print("\n  Power is preserved.")


def show_buyback(chain: str, price: Decimal | None) -> None:
    bb = api.buyback_stats(chain)
    complete = bb.complete_weeks()

    print(f"\nBuyback (treasury accrual, {chain})")
    if bb.total_accrued_gmx == 0:
        print("  nothing accrued on this chain. Buybacks run on Arbitrum.")
        return

    print(f"  weeks tracked       {bb.weeks_tracked} ({len(complete)} complete)")
    print(f"  total accrued       {bb.total_accrued_gmx.quantize(Decimal('0.01')):,} GMX")
    mean_all = bb.mean_weekly_gmx()
    mean_nz = bb.mean_weekly_gmx(exclude_zero=True)
    print(f"  mean / complete wk  {mean_all.quantize(Decimal('0.01')):,} GMX")
    print(f"  mean / active wk    {mean_nz.quantize(Decimal('0.01')):,} GMX")
    if price:
        print(f"  at ${price}: ~${(mean_all * price).quantize(Decimal('0.01')):,}/week deployed")

    print("\n  recent complete weeks:")
    for w in complete[-6:]:
        bar = "#" * int(min(40, w.gmx / max(mean_nz, Decimal(1)) * 20)) if w.gmx else ""
        print(f"    {_ts(w.start)}  {w.gmx.quantize(Decimal('0.01')):>12,} GMX  {bar}")

    partial = [w for w in bb.weeks if not w.is_complete()]
    for w in partial:
        print(f"    {_ts(w.start)}  {w.gmx.quantize(Decimal('0.01')):>12,} GMX  (week in progress)")

    trend = bb.trend()
    if "declining" in trend:
        print(f"\n  Accrual is {trend}. Buyback rate follows protocol fees, so a")
        print("  falling rate means falling fees, and a slower path to any price target.")

    zero_weeks = sum(1 for w in complete if w.gmx == 0)
    if zero_weeks:
        print(f"\n  {zero_weeks} of {len(complete)} complete weeks accrued nothing.")
        print("  Accrual is lumpy; do not extrapolate a smooth rate from the mean.")


def show_timeline(chain: str, price: Decimal | None) -> None:
    from . import forecast

    points = forecast.timeline(chain)
    if not points:
        print("\nNo completed buyback weeks on this chain.")
        return

    print(f"\nBuyback timeline ({chain}) - GMX bought and accrued to treasury")
    print(f"  {'week beginning':<16}{'weekly':>12}{'cumulative':>14}")
    peak = max(p.gmx for p in points) or Decimal(1)
    for p in points:
        bar = "#" * int(p.gmx / peak * 24)
        print(f"  {p.date:<16}{p.gmx:>12,.0f}{p.cumulative:>14,.0f}  {bar}")

    print("\n  weekly accrual, three readings:")
    for label, rate in forecast.accrual_rates(points).items():
        usd = f"  (~${rate * price:,.0f}/wk)" if price else ""
        print(f"    {label:<24}{rate:>10,.0f} GMX{usd}")

    change = forecast.trend_pct(points)
    if change is not None:
        direction = "down" if change < 0 else "up"
        print(f"\n  Across the last 5 complete weeks accrual is {direction} {abs(change):.0f}%.")
        print("  Accrual tracks protocol fees; it is not a fixed schedule.")


def show_forecast(chain: str, price: Decimal | None) -> None:
    from . import forecast

    points = forecast.timeline(chain)
    treasury = None
    try:
        sp = api.staking_power("0x" + "0" * 40, chain)
        if sp.position.treasury_gmx is not None:
            treasury = Decimal(sp.position.treasury_gmx) / WEI
    except (api.ApiError, ValueError):
        pass

    print(f"\nTreasury ({chain})")
    if treasury is None:
        print("  not reported on this chain.")
        return
    print(f"  balance now         {treasury:>12,.1f} GMX" + (f"  (~${treasury * price:,.0f})" if price else ""))
    print("  reported to 0.1 GMX, and it is not the balance of any address you can")
    print("  inspect, so it cannot be checked on-chain. Run --treasury for the trail.")

    if points:
        rates = forecast.accrual_rates(points)
        print("\n  Projected treasury, if accrual held at each rate (it has been falling):")
        print(f"    {'assumption':<24}{'+6 months':>14}{'+12 months':>14}")
        for label, rate in rates.items():
            six = forecast.project_treasury(treasury, rate, 26)
            twelve = forecast.project_treasury(treasury, rate, 52)
            print(f"    {label:<24}{six:>14,.0f}{twelve:>14,.0f}")

    print("\nTime to $90")
    if not price:
        print("  price unavailable; pass --price to compute.")
        return
    multiple = forecast.required_total_return(price)
    print(f"  price now {price:.4f}  ->  $90 needs a {multiple:.2f}x total return\n")
    print("  This is NOT a prediction. Buyback data cannot forecast price. Below is")
    print("  arithmetic on an assumed constant monthly return, which you choose:\n")
    print(f"    {'assumed monthly return':<26}{'months to $90':>15}{'reached':>14}")
    now = datetime.now(timezone.utc)
    for rate, months in forecast.scenario_table(price):
        if months is None:
            continue
        year = now.year + int((now.month - 1 + float(months)) // 12)
        month = int((now.month - 1 + float(months)) % 12) + 1
        print(f"    {rate * 100:>5.0f}%{'':<20}{float(months):>15.1f}{f'{year}-{month:02d}':>14}")
    print("\n  A constant monthly return is a modelling convenience, not how markets")
    print("  behave. Treat every row as 'if, then', never as 'when'.")


def show_supply(price: Decimal | None) -> None:
    from . import supply

    snap = supply.snapshot()
    print("\nGMX supply distribution (Arbitrum + Avalanche, from chain state)")
    print(f"  total supply        {snap.total_supply:>14,.0f} GMX\n")
    for b in snap.buckets:
        pct = b.gmx / snap.total_supply * 100 if snap.total_supply else Decimal(0)
        print(f"  {b.label:<26}{b.gmx:>12,.0f} {pct:>6.2f}%")
    print(f"  {'-' * 46}")
    print(f"  {'accounted':<26}{snap.accounted:>12,.0f} {snap.accounted / snap.total_supply * 100:>6.2f}%")
    residual_pct = snap.residual / snap.total_supply * 100 if snap.total_supply else Decimal(0)
    print(f"  {'unlabelled residual':<26}{snap.residual:>12,.0f} {residual_pct:>6.2f}%")

    print(f"\n  The residual is {snap.residual_note()}.")
    print("  We do not label exchange wallets: there is no verifiable public source")
    print("  for that mapping, and guessing would dress an assumption up as data.")
    print("  Supply a checked list in cex_addresses.json to break it out.")

    if snap.treasury_reported is not None:
        print(f"\n  Treasury (API, unverifiable): {snap.treasury_reported:,.1f} GMX"
              + (f"  ~${snap.treasury_reported * price:,.0f}" if price else ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gmx_power", description=__doc__.splitlines()[0])
    ap.add_argument("--address", help="staker address to inspect")
    ap.add_argument("--chain", default="arbitrum", choices=sorted(api.CHAINS))
    ap.add_argument("--unstake", type=Decimal, default=0, help="GMX to unstake (simulation)")
    ap.add_argument("--add", type=Decimal, default=0, help="GMX to add (simulation)")
    ap.add_argument("--buyback", action="store_true", help="show treasury buyback stats")
    ap.add_argument("--timeline", action="store_true", help="dated buyback timeline")
    ap.add_argument("--supply", action="store_true", help="where the GMX supply sits")
    ap.add_argument("--forecast", action="store_true", help="treasury projection and time-to-$90 arithmetic")
    ap.add_argument("--treasury", action="store_true", help="where the buyback sends GMX, read from the DataStore")
    ap.add_argument("--price", type=Decimal, help="override GMX price in USD")
    ap.add_argument("--staked", type=Decimal, help="model a hypothetical balance, no address needed")
    ap.add_argument("--peak", type=Decimal, help="hypothetical historical peak (defaults to --staked)")
    args = ap.parse_args(argv)

    views = (args.buyback, args.timeline, args.supply, args.forecast, args.treasury)
    if not args.address and args.staked is None and not any(views):
        ap.error("give --address, --staked, or one of --buyback/--timeline/--supply/--forecast/--treasury")
    if args.address and args.staked is not None:
        ap.error("--address and --staked are alternatives")

    price = args.price or api.gmx_price_usd(args.chain)
    if price is None and (args.forecast or args.supply):
        print("note: GMX price unavailable; USD figures omitted.", file=sys.stderr)

    if args.staked is not None:
        staked = int(args.staked * WEI)
        peak = int((args.peak if args.peak is not None else args.staked) * WEI)
        if peak < staked:
            ap.error("--peak cannot be below --staked")
        p = Position(staked, peak, 0, 1, None)
        print("Hypothetical position (no address; entitlement not modelled)")
        print(f"  staked              {_gmx(p.current_staked)} GMX")
        print(f"  historical peak     {_gmx(p.peak_staked)} GMX")
        print(f"  loyalty floor (80%) {_gmx(p.floor)} GMX")
        print(f"  safe to unstake     {_gmx(p.headroom)} GMX")
        if args.unstake or args.add:
            try:
                show_simulation(p, unstake=int(args.unstake * WEI), add=int(args.add * WEI))
            except ValueError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
        print("\nRead-only tool. Not advice.")
        return 0

    if args.address:
        try:
            sp = api.staking_power(args.address, args.chain)
        except (api.ApiError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        p = show_position(sp, price)
        if p.peak_staked == 0 and p.current_staked == 0:
            print("\n  This address has never staked GMX under the current regime.")
        elif args.unstake or args.add:
            unstake = int(args.unstake * WEI)
            add = int(args.add * WEI)
            try:
                show_simulation(p, unstake=unstake, add=add)
            except ValueError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1

    if args.buyback:
        show_buyback(args.chain, price)
    if args.timeline:
        show_timeline(args.chain, price)
    if args.supply:
        show_supply(price)
    if args.forecast:
        show_forecast(args.chain, price)
    if args.treasury:
        from . import treasury

        treasury.show()

    print("\nRead-only tool. Figures are the protocol's own, cross-checked; not advice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
