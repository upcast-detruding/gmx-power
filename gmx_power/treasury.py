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

None of that is evidence of anything improper. The DAO's stated plan is to buy
back GMX's supply held on centralised exchanges, which an on-chain arbitrage
contract cannot do. It does mean `treasuryGmxBalance` is an accounting figure
rather than the balance of an address you can inspect, so a staker's projected
share cannot presently be checked against the chain.

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

    stats = api.buyback_stats()
    print("\nWhat the API reports:")
    print(f"  totalAccrued             {stats.total_accrued_gmx:>14,.4f}")
    print(f"  over                     {stats.weeks_tracked:>14,} weeks")

    print("\nThese describe different things.")
    print("  The buying is not happening through these contracts, so `totalAccrued` is")
    print("  an accrued entitlement denominated in GMX, not the balance of an address")
    print("  you can inspect. The DAO's stated plan is to buy back supply held on")
    print("  centralised exchanges, which an on-chain arbitrage contract cannot do, so")
    print("  this is expected -- but it does mean the figure your projected share is")
    print("  computed against cannot be checked on-chain. Worth asking about; not by")
    print("  itself evidence that anything is wrong.")
    return 0


def main() -> int:
    return show()


if __name__ == "__main__":
    raise SystemExit(main())
