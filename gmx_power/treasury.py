"""Read the buyback's on-chain configuration, and compare it against the API.

GMX's fee split is encoded in the `DataStore`. Of each protocol fee, a
`*_FEE_RECEIVER_FACTOR` share (37%) is set aside, and `FeeHandler` splits that
share between a GMX buyback and a WNT buyback using `buybackGmxFactor(version)`.
V2's factor was 72.97%, and 0.37 x 0.7297 = **0.269989** -- that is where the DAO
plan's "27% of protocol fees" comes from. Note it is 26.9989%, not 27%: the
headline figure is the real one rounded to two decimal places.

`FeeHandler.buyback()` then lets anyone deposit a fixed `buybackBatchAmount` of
GMX in exchange for those fees, and `FeeHandler.withdrawFees()` forwards the
bought-back GMX to `dataStore.getAddress(Keys.FEE_RECEIVER)`.

This module reads all of it live. As of writing, `buybackGmxFactor` is **zero**
for both versions -- it was set to zero on 2026-03-11 (V2) and 2026-03-13 (V1),
one week after reward accrual began -- so the on-chain module buys no GMX at all,
and the fee share is taken as WNT instead.

The treasury's addresses *are* public -- not in the docs, but in the interface
repo, `src/domain/stats/treasury/useTreasury.ts`, which is what the stats page
sums. They are listed below. The app's "Total bought GMX" figure, however, is not
read from them: it is `totalAccrued` from `/v1/buyback/weekly-stats`.

None of that is evidence of anything improper. The DAO's stated plan is to buy
back GMX's supply held on centralised exchanges, which an on-chain arbitrage
contract cannot do, and GMX bought on an exchange need never touch these
addresses. It does mean `totalAccrued` is an accounting figure rather than
anything you can check on-chain, so a staker's projected share cannot presently
be verified.

    python -m gmx_power.treasury
"""

from __future__ import annotations

from decimal import Decimal

from . import rpc

WEI = 10**18

# gmx-synthetics, Arbitrum. From the published deployment manifests.
DATA_STORE = "0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8"
FEE_HANDLER = "0x7EB417637a3E6d1C19E6d69158c47610b7a5d9B3"
GMX_TOKEN = "0xfc5A1A6EB076a2C7aD06eD22C90d7E710E35ad0a"

SEL_GET_ADDRESS = "0x21f8a721"  # getAddress(bytes32)
SEL_GET_UINT = "0xbd02d0f5"  # getUint(bytes32)
SEL_BALANCE_OF = "0x70a08231"

# Uniswap V3 NonfungiblePositionManager. Some treasury GMX sits inside LP
# positions rather than as a plain balance, so a balance sum alone understates it.
UNIV3_POSITIONS = "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"

# The treasury. Not published in the docs, but public all the same: this is the
# list the stats page sums, from gmx-io/gmx-interface,
# src/domain/stats/treasury/useTreasury.ts
TREASURY_ADDRESSES = (
    "0x4bd1cdaab4254fc43ef6424653ca2375b4c94c0e",
    "0xc6378ddf536410c14666dc59bc92b5ebc0f2f79e",
    "0x0263ad94023a5df6d64f54bfef089f1fbf8a4ca0",
    "0xea8a734db4c7ea50c32b5db8a0cb811707e8ace3",
    "0xe1f7c5209938780625e354dc546e28397f6ce174",
    "0x68863dde14303bced249ca8ec6af85d4694dea6a",
    "0x0339740d92fb8baf73bab0e9eb9494bc0df1cafd",
    "0x2c247a44928d66041d9f7b11a69d7a84d25207ba",
    "0x0a2962120b11a4a36700c5de00d4980e58a2d1c0",
    "0xe57fe47902a35bc0d82c83e39610af546e1d18b9",
)

PRECISION = 10**30  # Precision.FLOAT_PRECISION: 1e30 == 100%

# Keys.sol: keccak256(abi.encode("FEE_RECEIVER"))
KEY_FEE_RECEIVER = "27b063950f8f840ec54a6c89264fe84c340d2e36c5461ab6fdfb822573067108"
# keccak256(abi.encode(WITHDRAWABLE_BUYBACK_TOKEN_AMOUNT, GMX))
KEY_WITHDRAWABLE_GMX = "b9a123e841c2e2a9ae6e3f605be46e1b17662eb1590bfc0bd8001858bb411b64"
# keccak256(abi.encode(BUYBACK_BATCH_AMOUNT, GMX))
KEY_BATCH_GMX = "d3e5af97fbd55e60584cd04c8aa2a96eb0e81b10e6178fc41fed3239c3e84e98"
# keccak256(abi.encode(BUYBACK_GMX_FACTOR, version)) -- the GMX share of the fee bucket
KEY_BUYBACK_GMX_FACTOR_V1 = "18c894c391181efbfb35ae67956508b7be98e2eb082023afcae4ed94c7f6d0ab"
KEY_BUYBACK_GMX_FACTOR_V2 = "493ac1d95c6487c680f3bf61423aed80575cf52d905f2a6d079f4eb5776c5de7"
# keccak256(abi.encode("POSITION_FEE_RECEIVER_FACTOR")) -- the fee bucket itself
KEY_POSITION_FEE_RECEIVER_FACTOR = "2b88ca05099085c49a5b3a47bec5166e91b81ecf14c8e7defd75f6a0e89886df"


def fee_receiver() -> str:
    """The address FeeHandler pays bought-back GMX to, read from the DataStore."""
    raw = rpc.call_raw("arbitrum", DATA_STORE, SEL_GET_ADDRESS + KEY_FEE_RECEIVER)
    if not raw:
        raise rpc.RpcError("DataStore returned no FEE_RECEIVER")
    return "0x" + raw[-40:]


def _uint(key: str) -> int:
    return rpc.call("arbitrum", DATA_STORE, SEL_GET_UINT + key) or 0


def _pct(raw: int) -> Decimal:
    return Decimal(raw) * 100 / PRECISION


def buyback_gmx_factors() -> tuple[int, int]:
    """The GMX share of the fee bucket, for V1 and V2. 1e30 == 100%."""
    return _uint(KEY_BUYBACK_GMX_FACTOR_V1), _uint(KEY_BUYBACK_GMX_FACTOR_V2)


def show() -> int:
    from . import api

    receiver = fee_receiver()
    receiver_gmx = rpc.balance_of("arbitrum", GMX_TOKEN, receiver)
    handler_gmx = rpc.balance_of("arbitrum", GMX_TOKEN, FEE_HANDLER)
    withdrawable = _uint(KEY_WITHDRAWABLE_GMX)
    batch = _uint(KEY_BATCH_GMX)
    fee_bucket = _uint(KEY_POSITION_FEE_RECEIVER_FACTOR)
    f_v1, f_v2 = buyback_gmx_factors()

    print("How the buyback is configured, read from the DataStore\n")
    print(f"  DataStore                {DATA_STORE}")
    print(f"  FeeHandler               {FEE_HANDLER}")
    print(f"  -> Keys.FEE_RECEIVER     {receiver}")
    print("     (dataStore.getAddress(keccak256(abi.encode(\"FEE_RECEIVER\"))))\n")

    print("The fee split:")
    print(f"  positionFeeReceiverFactor  {_pct(fee_bucket):>8.2f}%   of each fee is set aside")
    print(f"  buybackGmxFactor(v1)       {_pct(f_v1):>8.2f}%   of that share buys GMX")
    print(f"  buybackGmxFactor(v2)       {_pct(f_v2):>8.2f}%   of that share buys GMX")
    effective = Decimal(fee_bucket) * Decimal(f_v2) * 100 / (PRECISION * PRECISION)
    print(f"  -> effective V2 share      {effective:>8.2f}%   of protocol fees buying GMX")
    if f_v1 == 0 and f_v2 == 0:
        print("\n  Both factors are ZERO: the on-chain buyback module is buying no GMX.")
        print("  They were set to zero on 2026-03-11 (V2) and 2026-03-13 (V1). Before")
        print("  that, V2's factor was 72.97%: 0.37 x 0.7297 = 0.269989, the DAO plan's")
        print("  '27% of protocol fees' to two decimals. The share is now taken as WNT.")

    print("\nOn-chain GMX, right now:")
    print(f"  held by fee receiver     {Decimal(receiver_gmx) / WEI:>14,.4f}")
    print(f"  held by FeeHandler       {Decimal(handler_gmx) / WEI:>14,.4f}")
    print(f"  withdrawable from it     {Decimal(withdrawable) / WEI:>14,.4f}")
    print(f"  buyback batch size       {Decimal(batch) / WEI:>14,.4f}")

    print("\nThe treasury (gmx-interface, src/domain/stats/treasury/useTreasury.ts):")
    total = 0
    lp_positions = 0
    for addr in TREASURY_ADDRESSES:
        held = rpc.balance_of("arbitrum", GMX_TOKEN, addr)
        total += held
        n = rpc.call("arbitrum", UNIV3_POSITIONS, SEL_BALANCE_OF + rpc.pad_address(addr)) or 0
        lp_positions += n
        note = f"   + {n} Uniswap V3 position(s)" if n else ""
        print(f"  {addr}  {Decimal(held) / WEI:>14,.4f}{note}")
    print(f"  {'GMX held as plain balances':44s}{Decimal(total) / WEI:>14,.4f}")
    if lp_positions:
        print(f"  ...and {lp_positions} Uniswap V3 positions, whose GMX is *not* counted above.")

    stats = api.buyback_stats()
    print("\nWhat the API reports, and the app displays as \"Total bought GMX\":")
    print(f"  totalAccrued             {stats.total_accrued_gmx:>14,.4f}")
    print(f"  over                     {stats.weeks_tracked:>14,} weeks")

    print("\nThese describe different things.")
    print("  The app's \"Total bought GMX\" is this API figure, not a chain read. The")
    print("  buying is not happening through the contracts above, and `totalAccrued`")
    print("  matches neither the treasury's GMX balance nor the GMX flowing into it.")
    print("  It is an accrued entitlement denominated in GMX. The DAO's stated plan is")
    print("  to buy back supply held on centralised exchanges, which an on-chain")
    print("  arbitrage contract cannot do and which need never touch these addresses,")
    print("  so this is expected -- but it does mean the figure your projected share is")
    print("  computed against cannot be checked on-chain. Worth asking about; not by")
    print("  itself evidence that anything is wrong.")
    return 0


def main() -> int:
    return show()


if __name__ == "__main__":
    raise SystemExit(main())
