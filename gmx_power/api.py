"""Read-only client for the official GMX API.

Endpoint names were taken from the live spec at
https://{chain}.gmxapi.io/swagger.json, not from the docs site.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal

from .model import Position

CHAINS = {
    "arbitrum": "https://arbitrum.gmxapi.io/v1",
    "avalanche": "https://avalanche.gmxapi.io/v1",
}

_UA = {"User-Agent": "gmx-power-simulator (independent, read-only)"}


class ApiError(RuntimeError):
    pass


def _get(base: str, path: str, *, _attempts: int = 3, **params) -> dict | list:
    """GET with retry. The API intermittently 500s on valid requests."""
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=_UA)

    last: Exception | None = None
    for attempt in range(_attempts):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read()[:200].decode(errors="replace")
            last = ApiError(f"{path} -> HTTP {e.code}: {body}")
            if e.code < 500:  # 4xx is our fault; retrying will not help
                raise last from e
        except urllib.error.URLError as e:
            last = ApiError(f"{path} -> {e.reason}")
        if attempt < _attempts - 1:
            time.sleep(1.5 * (attempt + 1))
    raise last  # type: ignore[misc]


@dataclass(frozen=True)
class StakingPower:
    raw: dict

    @property
    def position(self) -> Position:
        r = self.raw
        treasury = r.get("treasuryGmxBalance")
        return Position(
            current_staked=int(r["currentStaked"]),
            peak_staked=int(r["historicalMaxStaked"] or 0),
            cumulative_power=int(r["cumulativePower"]),
            total_network_power=int(r["totalNetworkPower"]),
            treasury_gmx=None if treasury is None else int(treasury),
        )

    @property
    def reset_count(self) -> int:
        return int(self.raw.get("powerResetCount") or 0)

    @property
    def last_reset_at(self) -> int | None:
        return self.raw.get("lastPowerResetAt")

    @property
    def api_projected_gmx(self) -> Decimal | None:
        v = self.raw.get("projectedRewardShare")
        return None if v is None else Decimal(int(v)) / Decimal(10**18)

    @property
    def api_share_percent(self) -> Decimal:
        return Decimal(str(self.raw.get("userSharePercent") or 0))


def staking_power(address: str, chain: str = "arbitrum") -> StakingPower:
    if not (address.startswith("0x") and len(address) == 42):
        raise ValueError(f"not an address: {address!r}")
    return StakingPower(_get(CHAINS[chain], "/staking/power", address=address))


WEEK_SECONDS = 7 * 86400


@dataclass(frozen=True)
class Week:
    start: int
    end: int
    gmx: Decimal

    def is_complete(self, now: float | None = None) -> bool:
        """Complete iff a full week has elapsed since it began.

        Do not test `end <= now`: for the in-progress week the API sets `weekEnd`
        to the current time, so that comparison is always true and the partial
        week masquerades as a completed one with near-zero accrual.
        """
        return self.start + WEEK_SECONDS <= (time.time() if now is None else now)


@dataclass(frozen=True)
class BuybackStats:
    raw: dict

    @property
    def total_accrued_gmx(self) -> Decimal:
        return Decimal(int(self.raw["summary"]["totalAccrued"])) / Decimal(10**18)

    @property
    def weeks_tracked(self) -> int:
        return int(self.raw["summary"]["weeksTracked"])

    @property
    def weeks(self) -> list[Week]:
        return [
            Week(
                start=int(w["weekStart"]),
                end=int(w["weekEnd"]),
                gmx=Decimal(int(w["weeklyAccrued"])) / Decimal(10**18),
            )
            for w in self.raw["weeks"]
        ]

    def complete_weeks(self, now: float | None = None) -> list[Week]:
        """The in-progress week reads as a partial (often zero) figure. Drop it:
        including it understates the run rate and fakes a 'zero accrual' week."""
        return [w for w in self.weeks if w.is_complete(now)]

    def mean_weekly_gmx(self, *, exclude_zero: bool = False, now: float | None = None) -> Decimal:
        vals = [w.gmx for w in self.complete_weeks(now)]
        if exclude_zero:
            vals = [v for v in vals if v > 0]
        return sum(vals) / Decimal(len(vals)) if vals else Decimal(0)

    def trend(self, n: int = 5, now: float | None = None) -> str:
        """Direction of the last n complete weeks, stated plainly."""
        vals = [w.gmx for w in self.complete_weeks(now)][-n:]
        if len(vals) < 3:
            return "insufficient data"
        if all(b < a for a, b in zip(vals, vals[1:])):
            return f"declining every week for {len(vals)} weeks"
        if all(b > a for a, b in zip(vals, vals[1:])):
            return f"rising every week for {len(vals)} weeks"
        return "mixed"


def buyback_stats(chain: str = "arbitrum") -> BuybackStats:
    return BuybackStats(_get(CHAINS[chain], "/buyback/weekly-stats"))


def gmx_price_usd(chain: str = "arbitrum") -> Decimal | None:
    """Spot GMX price from the tickers feed, or None if unavailable.

    Tickers key on `symbol` (e.g. "GMX/USD [GMX-USDC]"), and `markPrice` is USD
    at 30 decimals. An earlier version looked for a `tokenSymbol` field that does
    not exist, so it always returned None and the CLI silently dropped every USD
    figure. Callers must say "unavailable" rather than omit.
    """
    try:
        tickers = _get(CHAINS[chain], "/markets/tickers")
    except ApiError:
        return None
    if not isinstance(tickers, list):
        return None
    for t in tickers:
        if str(t.get("symbol", "")).upper().startswith("GMX/USD"):
            raw = t.get("markPrice") or t.get("maxPrice")
            if raw:
                return Decimal(int(raw)) / Decimal(10**30)
    return None
