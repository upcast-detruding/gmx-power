"""Minimal JSON-RPC client with public endpoints by default.

Opsec: the tool must never require, embed, or print a private endpoint. Public
RPCs are the default and are sufficient for every read this package performs.
An endpoint may be supplied via GMX_RPC_URL / GMX_RPC_URL_AVALANCHE; if it is,
its URL is never logged, because those URLs usually carry the API key in the path.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

# A default user-agent gets 403'd by some public RPCs; a browser-ish one does not.
_HEADERS = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (gmx-power)"}

PUBLIC_RPC = {
    "arbitrum": [
        "https://arb1.arbitrum.io/rpc",
        "https://arbitrum-one.publicnode.com",
        "https://1rpc.io/arb",
    ],
    "avalanche": [
        "https://api.avax.network/ext/bc/C/rpc",
        "https://avalanche-c-chain-rpc.publicnode.com",
    ],
}

_ENV = {"arbitrum": "GMX_RPC_URL", "avalanche": "GMX_RPC_URL_AVALANCHE"}


class RpcError(RuntimeError):
    pass


def endpoints(chain: str) -> list[str]:
    """Private endpoint first if configured, then public fallbacks."""
    override = os.environ.get(_ENV[chain])
    return ([override] if override else []) + PUBLIC_RPC[chain]


def using_private_endpoint(chain: str) -> bool:
    return bool(os.environ.get(_ENV[chain]))


def _post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def call_raw(chain: str, to: str, data: str) -> str | None:
    """eth_call returning the hex returndata, or None if empty.

    Tries each endpoint in turn. Errors never quote the URL: it may hold a key.
    """
    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
               "params": [{"to": to, "data": data}, "latest"]}
    failures = 0
    for url in endpoints(chain):
        try:
            out = _post(url, payload)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            failures += 1
            continue
        if "error" in out:
            failures += 1
            continue
        result = out.get("result")
        return None if not result or result == "0x" else result
    raise RpcError(f"all {failures} {chain} endpoints failed (URLs withheld)")


def call(chain: str, to: str, data: str) -> int | None:
    """eth_call returning a single uint256, or None for empty returndata."""
    result = call_raw(chain, to, data)
    return None if result is None else int(result, 16)


def pad_address(addr: str) -> str:
    return addr[2:].lower().rjust(64, "0")


def balance_of(chain: str, token: str, holder: str) -> int:
    return call(chain, token, "0x70a08231" + pad_address(holder)) or 0


def total_supply(chain: str, token: str) -> int:
    return call(chain, token, "0x18160ddd") or 0
