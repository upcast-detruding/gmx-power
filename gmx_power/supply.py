"""Where the GMX supply actually sits: staked, in LPs, in the treasury, elsewhere.

Every figure here is read from chain state, with one exception: the treasury,
which the API reports but whose address GMX has not published. The treasury is
therefore *inside* the unlabelled residual, not a separate line we can verify.

On exchange balances. Reporting "GMX on CEXes" needs a labelled address set, and
we have no verifiable source for one. Guessing which hot wallet belongs to which
exchange would be an unsupported assertion dressed as data, so this module does
not do it. Instead it reports what it can prove - total supply, staked, LP pools,
vester - and calls the remainder what it is: unlabelled. Supply a verified list in
`cex_addresses.json` and it will be subtracted from the residual and shown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from . import api, rpc

WEI = 10**18

# Public GMX protocol contracts.
ARBITRUM = {
    "gmx": "0xfc5A1A6EB076a2C7aD06eD22C90d7E710E35ad0a",
    "staked_gmx_tracker": "0x908C4D94D34924765f1eDc22A1DD098397c59dD4",
    "gmx_vester": "0x199070DDfd1CFb69173aa2F7e20906F26B363004",
    "uniswap_gmx_eth": "0x80A9ae39310abf666A87C743d6ebBD0E8C42158E",
}
AVALANCHE = {
    "gmx": "0x62edc0692BD897D2295872a9FFCac5425011c661",
    "staked_gmx_tracker": "0x2bD10f8E93B3669b6d42E74eEedC65dd1B0a1342",
    "traderjoe_gmx_avax": "0x0c91a070f862666bbcce281346be45766d874d98",
}


@dataclass
class Bucket:
    label: str
    gmx: Decimal
    verified: bool = True


@dataclass
class SupplySnapshot:
    total_supply: Decimal
    buckets: list[Bucket] = field(default_factory=list)
    treasury_reported: Decimal | None = None

    @property
    def accounted(self) -> Decimal:
        return sum((b.gmx for b in self.buckets), Decimal(0))

    @property
    def residual(self) -> Decimal:
        return self.total_supply - self.accounted

    def residual_note(self) -> str:
        parts = ["CEX balances", "self-custody", "bridges"]
        if self.treasury_reported is not None:
            parts.append("the treasury")
        return ", ".join(parts)


def _gm_market_tokens(chain: str = "arbitrum") -> list[tuple[str, str]]:
    """GM pools whose long token is GMX, from the tickers feed."""
    try:
        tickers = api._get(api.CHAINS[chain], "/markets/tickers")
    except api.ApiError:
        return []
    out = []
    for t in tickers if isinstance(tickers, list) else []:
        symbol = str(t.get("symbol", ""))
        if symbol.upper().startswith("GMX/USD") and t.get("marketTokenAddress"):
            out.append((symbol, t["marketTokenAddress"]))
    return out


def _labelled_cex() -> dict[str, str]:
    """Optional user-supplied {address: label}. We ship none; see module docstring."""
    path = Path(__file__).resolve().parent.parent / "cex_addresses.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def snapshot() -> SupplySnapshot:
    """Arbitrum + Avalanche GMX supply, bucketed."""
    gmx_a, gmx_v = ARBITRUM["gmx"], AVALANCHE["gmx"]
    total = Decimal(rpc.total_supply("arbitrum", gmx_a) + rpc.total_supply("avalanche", gmx_v)) / WEI

    def held(chain: str, token: str, holder: str) -> Decimal:
        return Decimal(rpc.balance_of(chain, token, holder)) / WEI

    buckets = [
        Bucket("staked (Arbitrum)", held("arbitrum", gmx_a, ARBITRUM["staked_gmx_tracker"])),
        Bucket("staked (Avalanche)", held("avalanche", gmx_v, AVALANCHE["staked_gmx_tracker"])),
        Bucket("GmxVester (vesting GMX)", held("arbitrum", gmx_a, ARBITRUM["gmx_vester"])),
        Bucket("Uniswap GMX/ETH", held("arbitrum", gmx_a, ARBITRUM["uniswap_gmx_eth"])),
        Bucket("TraderJoe GMX/AVAX", held("avalanche", gmx_v, AVALANCHE["traderjoe_gmx_avax"])),
    ]
    for symbol, market in _gm_market_tokens():
        buckets.append(Bucket(f"GM {symbol}", held("arbitrum", gmx_a, market)))

    for address, label in _labelled_cex().items():
        buckets.append(Bucket(f"CEX: {label}", held("arbitrum", gmx_a, address)))

    treasury = None
    try:
        sp = api.staking_power("0x" + "0" * 40)
        if sp.position.treasury_gmx is not None:
            treasury = Decimal(sp.position.treasury_gmx) / WEI
    except (api.ApiError, ValueError):
        pass

    return SupplySnapshot(total_supply=total, buckets=buckets, treasury_reported=treasury)
