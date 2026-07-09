"""The DataStore keys are hardcoded, so pin them.

A wrong key does not error: `getAddress` returns the zero address and `getUint`
returns 0, which would look like a real answer. These constants are keccak256 of
the ABI encoding, recomputed here from a self-contained keccak so a typo cannot
survive. The vectors at the bottom check the keccak itself.
"""

from __future__ import annotations

from gmx_power import treasury

_RC = [0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
       0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
       0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
       0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
       0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
       0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008]
_ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61],
        [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
_M = (1 << 64) - 1


def _rl(x: int, n: int) -> int:
    return ((x << n) | (x >> (64 - n))) & _M


def keccak256(data: bytes) -> bytes:
    a = [[0] * 5 for _ in range(5)]
    p = bytearray(data) + b"\x01"
    while len(p) % 136:
        p += b"\x00"
    p[-1] ^= 0x80
    for off in range(0, len(p), 136):
        for i in range(17):
            a[i % 5][i // 5] ^= int.from_bytes(p[off + i * 8: off + i * 8 + 8], "little")
        for rnd in range(24):
            c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
            d = [c[(x - 1) % 5] ^ _rl(c[(x + 1) % 5], 1) for x in range(5)]
            for x in range(5):
                for y in range(5):
                    a[x][y] ^= d[x]
            b = [[0] * 5 for _ in range(5)]
            for x in range(5):
                for y in range(5):
                    b[y][(2 * x + 3 * y) % 5] = _rl(a[x][y], _ROT[x][y])
            for x in range(5):
                for y in range(5):
                    a[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y] & _M) & b[(x + 2) % 5][y])
            a[0][0] ^= _RC[rnd]
    return b"".join(a[i % 5][i // 5].to_bytes(8, "little") for i in range(4))[:32]


def _abi_encode_string(s: bytes) -> bytes:
    return (32).to_bytes(32, "big") + len(s).to_bytes(32, "big") + s + b"\x00" * (-len(s) % 32)


def _abi_address(addr: str) -> bytes:
    return bytes.fromhex(addr[2:].lower().rjust(64, "0"))


def test_keccak_matches_published_vectors():
    assert keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert keccak256(b"balanceOf(address)")[:4].hex() == "70a08231"


def test_selectors():
    assert treasury.SEL_GET_ADDRESS == "0x" + keccak256(b"getAddress(bytes32)")[:4].hex()
    assert treasury.SEL_GET_UINT == "0x" + keccak256(b"getUint(bytes32)")[:4].hex()


def test_fee_receiver_key():
    expected = keccak256(_abi_encode_string(b"FEE_RECEIVER"))
    assert treasury.KEY_FEE_RECEIVER == expected.hex()


def test_gmx_scoped_keys():
    """keccak256(abi.encode(CONSTANT, token)) -- a hash of a hash and an address."""
    gmx = _abi_address(treasury.GMX_TOKEN)
    withdrawable = keccak256(_abi_encode_string(b"WITHDRAWABLE_BUYBACK_TOKEN_AMOUNT"))
    batch = keccak256(_abi_encode_string(b"BUYBACK_BATCH_AMOUNT"))
    assert treasury.KEY_WITHDRAWABLE_GMX == keccak256(withdrawable + gmx).hex()
    assert treasury.KEY_BATCH_GMX == keccak256(batch + gmx).hex()


def test_version_scoped_buyback_factor_keys():
    """keccak256(abi.encode(BUYBACK_GMX_FACTOR, version)) -- version is a uint256."""
    base = keccak256(_abi_encode_string(b"BUYBACK_GMX_FACTOR"))
    assert treasury.KEY_BUYBACK_GMX_FACTOR_V1 == keccak256(base + (1).to_bytes(32, "big")).hex()
    assert treasury.KEY_BUYBACK_GMX_FACTOR_V2 == keccak256(base + (2).to_bytes(32, "big")).hex()


def test_position_fee_receiver_factor_key():
    expected = keccak256(_abi_encode_string(b"POSITION_FEE_RECEIVER_FACTOR"))
    assert treasury.KEY_POSITION_FEE_RECEIVER_FACTOR == expected.hex()


def test_the_27_percent_arithmetic():
    """How the DAO plan's "27% of fees" was encoded on-chain -- and it is not exact.

    0.37 x 0.7297 = 0.269989, not 0.27. The headline figure is the true one rounded
    to two decimal places. Worth pinning: the whole point of this tool is not to
    round away the difference between a stated number and a real one.
    """
    fee_bucket = 37 * treasury.PRECISION // 100
    gmx_share = 7297 * treasury.PRECISION // 10000
    effective = fee_bucket * gmx_share // treasury.PRECISION
    assert effective == 269989 * treasury.PRECISION // 1_000_000  # 26.9989%
    assert effective != 27 * treasury.PRECISION // 100
    assert round(effective * 100 / treasury.PRECISION, 2) == 27.00


def test_keys_are_bare_hex_not_prefixed():
    """They are concatenated onto a selector, so a 0x prefix would corrupt the call."""
    keys = (
        treasury.KEY_FEE_RECEIVER,
        treasury.KEY_WITHDRAWABLE_GMX,
        treasury.KEY_BATCH_GMX,
        treasury.KEY_BUYBACK_GMX_FACTOR_V1,
        treasury.KEY_BUYBACK_GMX_FACTOR_V2,
        treasury.KEY_POSITION_FEE_RECEIVER_FACTOR,
    )
    for key in keys:
        assert len(key) == 64 and not key.startswith("0x")
    assert len(set(keys)) == len(keys), "duplicate key: a copy-paste error"
