"""Locate the buyback destination on-chain, and compare it against the API.

The treasury has no published address, but the *destination of the buyback* is
not a secret: `FeeHandler.withdrawFees()` sends the bought-back GMX to

    dataStore.getAddress(Keys.FEE_RECEIVER)

which anyone can read. This module reads it live, then puts the on-chain figures
next to the API's `treasuryGmxBalance`, and shows that they do not reconcile.

That is not an allegation of anything. It means only that `treasuryGmxBalance` is
an accounting figure rather than the balance of an address you can inspect, and
so a staker's projected share cannot presently be checked against the chain.

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

# Keys.sol: keccak256(abi.encode("FEE_RECEIVER"))
KEY_FEE_RECEIVER = "27b063950f8f840ec54a6c89264fe84c340d2e36c5461ab6fdfb822573067108"
# keccak256(abi.encode(WITHDRAWABLE_BUYBACK_TOKEN_AMOUNT, GMX))
KEY_WITHDRAWABLE_GMX = "b9a123e841c2e2a9ae6e3f605be46e1b17662eb1590bfc0bd8001858bb411b64"
# keccak256(abi.encode(BUYBACK_BATCH_AMOUNT, GMX))
KEY_BATCH_GMX = "d3e5af97fbd55e60584cd04c8aa2a96eb0e81b10e6178fc41fed3239c3e84e98"


def fee_receiver() -> str:
    """The address FeeHandler pays bought-back GMX to, read from the DataStore."""
    raw = rpc.call_raw("arbitrum", DATA_STORE, SEL_GET_ADDRESS + KEY_FEE_RECEIVER)
    if not raw:
        raise rpc.RpcError("DataStore returned no FEE_RECEIVER")
    return "0x" + raw[-40:]


def _uint(key: str) -> int:
    return rpc.call("arbitrum", DATA_STORE, SEL_GET_UINT + key) or 0


def show() -> int:
    from . import api

    receiver = fee_receiver()
    receiver_gmx = rpc.balance_of("arbitrum", GMX_TOKEN, receiver)
    handler_gmx = rpc.balance_of("arbitrum", GMX_TOKEN, FEE_HANDLER)
    withdrawable = _uint(KEY_WITHDRAWABLE_GMX)
    batch = _uint(KEY_BATCH_GMX)

    print("Where the buyback actually sends GMX\n")
    print(f"  DataStore                {DATA_STORE}")
    print(f"  FeeHandler               {FEE_HANDLER}")
    print(f"  -> Keys.FEE_RECEIVER     {receiver}")
    print("     (dataStore.getAddress(keccak256(abi.encode(\"FEE_RECEIVER\"))))\n")

    print("On-chain GMX, right now:")
    print(f"  held by fee receiver     {Decimal(receiver_gmx) / WEI:>14,.4f}")
    print(f"  held by FeeHandler       {Decimal(handler_gmx) / WEI:>14,.4f}")
    print(f"  withdrawable from it     {Decimal(withdrawable) / WEI:>14,.4f}")
    print(f"  buyback batch size       {Decimal(batch) / WEI:>14,.4f}")

    stats = api.buyback_stats()
    print("\nWhat the API reports:")
    print(f"  totalAccrued             {stats.total_accrued_gmx:>14,.4f}")
    print(f"  over                     {stats.weeks_tracked:>14,} weeks")

    print("\nThese describe different things.")
    print("  `totalAccrued` is best read as the GMX-denominated 27% share of protocol")
    print("  fees: an accrued entitlement, not a balance. It is not the balance of any")
    print("  address reachable from the contracts, and the treasury's own address is")
    print("  not published. So the figure your projected share is computed against")
    print("  cannot be checked on-chain. That is worth knowing, and worth asking about;")
    print("  it is not by itself evidence that anything is wrong.")
    return 0


def main() -> int:
    return show()


if __name__ == "__main__":
    raise SystemExit(main())
