# Verification

Why you can trust this tool's arithmetic: because none of it is taken on faith. Every
claim below was checked against the live API and Arbitrum chain state, and every check
is reproducible with a command in this repository.

Figures are from **2026-07-09** and will have moved. The methods have not.

## Finding the endpoints

The documentation site renders client-side and yields nothing to a fetcher, and the
REST docs live at `/docs/api/rest`. The API publishes its own spec:

```
https://arbitrum.gmxapi.io/swagger.json      # 38 paths
```

The two that matter:

| Endpoint | Purpose |
|---|---|
| `GET /v1/staking/power?address=0x…` | the whole entitlement picture for one address |
| `GET /v1/buyback/weekly-stats` | weekly and cumulative treasury accrual |

`StakingPowerResponse` carries `cumulativePower`, `totalNetworkPower`,
`userSharePercent`, `historicalMaxStaked`, `currentStaked`, `loyaltyRatio`,
`lastPowerResetAt`, `powerResetCount`, `projectedRewardShare`, `treasuryGmxBalance`,
`powerAccrualStart` and `loyaltyTrackingStart`.

The API intermittently returns HTTP 500 on valid requests. The client retries 5xx.

## Protocol constants, confirmed

- `powerAccrualStart` = **1772582400** = 2026-03-04T00:00:00Z
- `loyaltyTrackingStart` = **1774396800** = 2026-03-25T00:00:00Z, exactly three weeks later

Both match the governance announcements, which is evidence the API is authoritative
rather than a stale mirror.

## Power is measured in wei-seconds — proven, not assumed

The docs give the formula, `accumulatedPower += currentBalance × Δt`, but not the unit
the API reports it in. `totalNetworkPower` accrues in real time, so if power is the
time-integral of the staked balance then `dP/dt` must equal the network's staked
balance. Sampling 150 seconds apart:

```
dP/dt      = 7,194,680 GMX   (the derivative)
P/elapsed  = 7,088,882 GMX   (the long-run integral, since 2026-03-04)
```

Two independent routes to the same quantity, agreeing to 1.5%; the gap is real, since
stake has drifted since March. A wei-**days** reading would imply ~614 billion GMX
staked against a ~11.6M supply, and is excluded.

```
python -m gmx_power.verify
```

## What counts as "staked": GMX **and** esGMX

The docs say power counts *"both GMX and esGMX in the StakedGmxTracker"*. Checking that
independently, against what the tracker actually holds:

| Candidate | Amount | Ratio to dP/dt | Verdict |
|---|---|---|---|
| GMX only | 6,153,611 | 0.855 | **excluded** |
| GMX + esGMX | 7,198,319 | **1.0005** | match |
| sGMX totalSupply | 7,187,416 | 0.9990 | match |

GMX-only is off by 14%, far beyond measurement noise. Contracts were identified by
calling `name()` and `symbol()`, not by trusting a list.

```
python -m gmx_power.chain
```

At account level the accounting is exact:
`depositBalances(acct, GMX) + depositBalances(acct, esGMX) == stakedAmounts(acct)`,
which is precisely what the API reports as `currentStaked`. And the API's
`projectedRewardShare` is reproduced to four decimals by
`treasuryGmxBalance × cumulativePower / totalNetworkPower`.

## Vesting esGMX lowers the balance the loyalty rule watches

Established from contract source, then confirmed against the deployed contracts:

1. `Vester._deposit` calls `IERC20(esToken).safeTransferFrom(_account, …)` — the esGMX
   must already sit in your wallet.
2. Staked esGMX is held by `stakedGmxTracker`, so it must be unstaked first.
3. The only route is `RewardRouterV2.unstakeEsGmx` → `_unstakeGmx(…, esGmx, …)` →
   `stakedGmxTracker.unstakeForAccount` → `RewardTracker._unstake`, which executes
   `stakedAmounts[_account] = stakedAmount.sub(_amount)`.
4. `stakedAmounts` is `currentStaked`, and `currentStaked / historicalMaxStaked` is the
   loyalty ratio.
5. **No bypass exists.** `RewardRouterV2` exposes no vest-from-staked function, and
   `Vester.depositForAccount` is handler-gated.

Deployed `GmxVester` wiring, read live: `esToken()` = esGMX, `pairToken()` =
feeGmxTracker, `claimableToken()` = GMX, `rewardTracker()` = stakedGmxTracker.
`stakedGmxTracker.isDepositToken` is true for both GMX and esGMX.

Three caps bind a vest, and the smallest wins: the esGMX you have staked, your loyalty
headroom, and `Vester.getMaxVestableAmount` (the docs' *"capped to the esGMX rewards
received by that account"*). For an esGMX-heavy position the loyalty headroom usually
binds first, well below what the vester would otherwise allow.

Reserving `pairToken` for a vest locks fee-tracker tokens but does **not** touch
`stakedAmounts`, so it adds no reset risk. Unstaking does burn bnGMX multiplier points
proportionally (`_unstakeGmx`), which is a real cost even within the headroom, and is
not modelled here.

## The entitlement is computed off-chain

`gmx-contracts/contracts/staking/` holds `RewardTracker`, `RewardDistributor`,
`Vester`, `RewardRouterV2` and friends — and **no staking-power or loyalty contract**.
Power, the historical peak, and the reset rule are computed by the backend from chain
state. The treasury sits as a balance awaiting a future distribution mechanism.

The entitlement is therefore **verifiable but not trustlessly enforced.** Power is an
integral over stake and unstake events, so it can be reconstructed independently. That
reconstruction is the natural next thing to build here.

Two related observations:

- `treasuryGmxBalance` is reported as exactly `334390900000000000000000` — 17 trailing
  zeros — and `totalAccrued` as exactly `334391000000000000000000`. Real ERC-20
  balances do not end in seventeen zeros. Both are **rounded**, the 0.1 GMX difference
  between them is only that rounding, and neither can be reconciled to the wei.
- The treasury address is not published in the SDK's contract config, and did not
  surface in a sample of recent GMX transfers. Without it, the treasury side cannot be
  checked at all.

## Accrual is per-chain, and Arbitrum-only

| | Arbitrum | Avalanche |
|---|---|---|
| `totalAccrued` | 334,391 GMX | **0** |
| `treasuryGmxBalance` | 334,390.9 GMX | **null** |
| `totalNetworkPower` | 7.81e31 | 4.42e30 |

Avalanche runs **its own power pool** (implying ~403k GMX staked) but holds no treasury.
How the treasury is divided between chains at distribution is not documented. A tool
that showed an Avalanche staker a "projected claim" against the Arbitrum treasury would
be lying, so this one returns nothing there.

## Two traps in the data

**The in-progress week reports `weekEnd` as the current time.** Testing `end <= now`
therefore marks it complete, and a partial week with near-zero accrual poses as a
completed zero-accrual week — dragging down every mean taken over it. Completeness must
be derived from `weekStart`. Pinned by `tests/test_buyback.py`.

**Buyback accrual is lumpy and currently falling.** Complete weeks, GMX accrued:

```
2026-06-03   33,820
2026-06-10   29,460
2026-06-17   25,630
2026-06-24   23,280
2026-07-01   20,740
```

Down 39% across five weeks. Accrual tracks protocol fees, so a falling rate means a
slower path to any price target. Several earlier weeks accrued nothing at all. Do not
extrapolate a smooth rate from a mean.

## A note on published figures

Reported buyback totals reconcile with the API. A widely-quoted "168,500 GMX
repurchased, ~$1.1M deployed, blended average ~$6.50" matches the API's cumulative
accrual at the week ending 2026-05-06 — **168,501 GMX**, a difference of one GMX — and
`$1,100,000 ÷ 168,500 = $6.53`. The figures are consistent. They are frequently
mis-dated to an earlier announcement that contains no numbers at all.
