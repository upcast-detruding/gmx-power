# gmx-power

A read-only tool for GMX stakers under the reward regime that began **2026-03-04**.

Staking rewards no longer go to stakers directly. 27% of protocol fees buy GMX on the
open market, the GMX accrues to a treasury, and it is distributed only once GMX
exceeds **$90** — pro-rata by *staking power* (balance × time), under an
**80%-of-peak loyalty rule** that resets accumulated power to zero if breached.

The official dApp shows your current state. It does not let you ask *"what happens if
I unstake 500 GMX?"* **before** you sign. This does.

It never signs a transaction, never sends one, never asks for a private key, and has
no backend, no telemetry, and no analytics.

## Install

Python 3.10+. No dependencies. `pytest` only to run the tests.

```
git clone <this repo> && cd gmx-power
python -m gmx_power --help
```

## Use

Inspect a position, and simulate a change before making it:

```
python -m gmx_power --address 0xYourAddress
python -m gmx_power --address 0xYourAddress --unstake 500
python -m gmx_power --address 0xYourAddress --add 1000
```

Model a hypothetical position without revealing any address:

```
python -m gmx_power --staked 1000 --peak 1200 --unstake 250
```

Protocol-wide views:

```
python -m gmx_power --buyback     # weekly accrual summary
python -m gmx_power --timeline    # dated buyback history, week by week
python -m gmx_power --supply      # staked / LP / vester buckets, and the residual
python -m gmx_power --forecast    # treasury projection, and time-to-$90 arithmetic
```

Reproduce the checks the tool rests on:

```
python -m gmx_power.verify        # proves power is wei-seconds, from the live counter
python -m gmx_power.chain         # what counts as "staked", read from chain state
python -m pytest tests/
```

Chain reads use public RPC endpoints and need no API key. `GMX_RPC_URL` may override
the Arbitrum endpoint; it is never printed, since such URLs commonly embed the key in
the path.

## Three rules worth understanding before you act

All three are stated in the [rewards documentation](https://docs.gmx.io/docs/tokenomics/rewards/).
This tool does not reveal them. It lets you put your own numbers on them, and it
independently checks that the protocol's backend behaves as documented — see
[VERIFICATION.md](VERIFICATION.md).

**Staked esGMX counts toward your power.** Vesting esGMX requires unstaking it first,
which lowers the balance the loyalty rule watches. Vest more than your headroom and
you reset your power to zero, forfeiting the entire accrued claim. Easy to walk into
if your position is mostly esGMX.

**Topping up raises the floor.** The floor tracks your *historical peak*. Add 1,000
GMX while sitting at your peak and you gain only 200 GMX of extra withdrawal headroom,
not 1,000. At your peak you can never withdraw more than 20%.

**Unstaking never lowers your peak.** Spend your 20% and your headroom is zero until
you stake more. There is no second bite. Unstaking also burns multiplier points
(bnGMX) proportionally, a cost that applies even within the headroom.

## What this tool refuses to do

**It will not tell you when GMX reaches $90.** Buyback accrual is observable and
extrapolates under stated assumptions. Price does not. `--forecast` asks *you* for an
assumed monthly return and does the arithmetic, so every row reads "if, then" and
never "when". Change the assumption and the answer moves — which is the honest shape
of the question.

**It will not report GMX held on exchanges.** That needs a labelled address set, and
no verifiable public source for one exists. Guessing which hot wallet belongs to which
exchange would be an assumption dressed as data. `--supply` proves what it can — staked,
LP pools, vester — and reports the rest as an unlabelled residual. Drop a list you have
checked yourself into `cex_addresses.json` and it will be broken out.

**It will not show you a number it cannot stand behind.** An Avalanche staker has no
reported treasury to claim against, so the tool prints nothing there rather than a
plausible figure. The treasury balance is reported rounded by the API, and its address
is unpublished, so it is labelled unverifiable.

## Contributing

Bug reports and patches welcome. The tests pin the boundaries that matter — the
one-wei reset threshold, the partial-week trap, the null-treasury path. Please add a
test with any behaviour change.

## Licence

MIT. See [LICENSE](LICENSE).

## Disclaimer

Informational, for people who already stake GMX. Not financial advice, not a
recommendation to buy, sell, or hold anything, and not affiliated with GMX Labs.
Verify anything that matters against the protocol's own contracts.
