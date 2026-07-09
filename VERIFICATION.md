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
`stakedAmounts`, so it adds no reset risk.

`_unstakeGmx` also burns bnGMX (multiplier points) proportionally, but that is not a
cost worth modelling: multiplier points were retired by governance in May 2024 and
converted to esGMX at 25:1. On-chain today, `tokensPerInterval` is zero on the bnGMX,
WETH and esGMX distributors, and `extendedGmxTracker.totalDepositSupply(bnGMX)` is
zero — nothing is emitted, nothing is staked, and the remaining ~12.3M bnGMX supply
sits inert in wallets. Burning it costs nothing.

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
  zeros — and `totalAccrued` as exactly `334391000000000000000000`. Every
  `weeklyAccrued` is likewise a whole number of GMX. Real ERC-20 balances do not look
  like this. All are **rounded**, the 0.1 GMX difference between the first two is only
  that rounding, and neither can be reconciled to the wei.
- The treasury's addresses **are** public — not in the docs, but in the interface repo,
  `src/domain/stats/treasury/useTreasury.ts`, which is the list the stats page sums.
  There are ten. `python -m gmx_power.treasury` reads their GMX balances live.

### How the 27% is encoded, and why you cannot currently see it

The "27% of protocol fees" is not a constant anywhere. Of each fee,
`positionFeeReceiverFactor` sets aside **37%**. `FeeHandler` splits that share between a
GMX buyback and a WNT buyback via `buybackGmxFactor(version)`. V2's factor was
**72.97%**, and `0.37 × 0.7297 = 0.269989` — the headline 27%, to two decimal places.
Arbitrageurs call `FeeHandler.buyback()`, depositing a fixed `buybackBatchAmount`
(200 GMX) for the accrued fee tokens; `FeeHandler.withdrawFees()` then forwards the GMX
to `dataStore.getAddress(Keys.FEE_RECEIVER)`.

Today both factors read **zero**. They were set to zero by `Config.setUint` on
**2026-03-11** (V2, block 440,628,485) and **2026-03-13** (V1, block 441,297,314), a week
after accrual began, and the fee share is taken as WNT instead. The flows agree: since
2026-03-04 the `FeeHandler` has delivered **2,000 GMX** to the fee receiver — residue
draining in 200-GMX batches — against **1,453.71 WETH** over the same period, while
`totalAccrued` reports **334,391 GMX**.

This is **not** evidence of anything improper. The DAO's stated plan is to buy back
supply held on centralised exchanges, which an on-chain arbitrage contract cannot do.
`totalAccrued` therefore reads as an accrued entitlement denominated in GMX rather than
a wallet balance, consistent with the treasury being held as the floor price fund and
protocol-owned liquidity.

### The dashboard is not a second source

`app.gmx.io` shows **"Total bought GMX"** with the tooltip *"Total amount of GMX bought
back since tracking began"*. That figure is not read from the chain: `useBuybackWeeklyStats`
calls `sdk.fetchBuybackWeeklyStats()`, which is `GET /v1/buyback/weekly-stats`, hardcoded
to Arbitrum. It is the same `totalAccrued` this tool prints. Confirming it against the
API confirms nothing.

### It matches neither the treasury's balance nor its inflows

Summing GMX across the ten published treasury addresses (Arbitrum): **306,120.03 GMX**
as plain balances, plus **275,000 GMX** inside eleven Uniswap V3 positions — all
out-of-range, single-sided range orders — for **581,120.03 GMX**. More than
`totalAccrued`, because the treasury held GMX long before the buyback began.

Tracking GMX *into* those addresses since 2026-03-04, excluding transfers between them:
**140,545.08 GMX**. Of that, 97,741.30 arrived on 2026-03-11/12 from the Uniswap V3
position manager and from the pool itself — a `Burn` and a `Collect`, with no `Swap`
event, so liquidity being withdrawn rather than GMX being bought — and 3,967.07 arrived
on 2026-05-28. Only **38,836.51** came from the fee receiver. Nothing resembles a
recurring purchase of ~18,000 GMX a week.

So `totalAccrued` is an accrued entitlement denominated in GMX. The tool labels the
treasury side unverifiable and says so, rather than implying a precision it lacks.

One check cuts the other way and is worth recording: the fee receiver switched its GMX
destination at block **438,078,860** — **2026-03-04 01:00 UTC**, the first hour after
`powerAccrualStart`, having paid the previous address until thirty seconds earlier. The
regime change is visible on-chain to the hour.

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
