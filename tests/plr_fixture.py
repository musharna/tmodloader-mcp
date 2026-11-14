"""Build encrypted `.plr` files for tests, with an encryptor of its own.

Not a test module (no `test_` prefix): `test_inventory.py` and
`test_mcp_protocol.py` import it to write character files the reader has to
DECRYPT, rather than empty files a stem-listing would pass on.

THE ENCRYPTOR SHARES NO CODE WITH THE READER. It computes its own S-box (by
searching for each inverse, where the reader uses log tables), runs the
forward cipher where the reader runs the inverse one, and is pinned to the
FIPS-197 known-answer vector by `test_inventory.py`. A shared table would let
one typo agree with itself; two derivations and a published vector cannot.
What neither can prove is that Terraria uses this format, which is what
`live_player_names_check.py` checks against real files.
"""

from __future__ import annotations

import struct

PLR_KEY = "h3y_gUyZ".encode("utf-16-le")


def _gmul(a: int, b: int) -> int:
    out = 0
    for _ in range(8):
        if b & 1:
            out ^= a
        carry = a & 0x80
        a = (a << 1) & 0xFF
        if carry:
            a ^= 0x1B
        b >>= 1
    return out


def _make_sbox() -> list[int]:
    box = []
    for a in range(256):
        inv = next((b for b in range(1, 256) if _gmul(a, b) == 1), 0)
        s = 0x63
        for bit in range(8):
            v = 0
            for k in (0, 4, 5, 6, 7):
                v ^= (inv >> ((bit + k) % 8)) & 1
            s ^= v << bit
        box.append(s)
    return box


SBOX = _make_sbox()


def _round_keys(key: bytes) -> list[list[int]]:
    w = [list(key[i : i + 4]) for i in range(0, 16, 4)]
    rc = 1
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = [SBOX[x] for x in t[1:] + t[:1]]
            t[0] ^= rc
            rc = _gmul(rc, 2)
        w.append([x ^ y for x, y in zip(w[i - 4], t, strict=True)])
    return [[b for word in w[r * 4 : r * 4 + 4] for b in word] for r in range(11)]


def encrypt_block(key: bytes, block: bytes) -> bytes:
    """FIPS-197 Cipher. The state is indexed [row][column]."""
    keys = _round_keys(key)
    st = [[block[c * 4 + r] for c in range(4)] for r in range(4)]

    def add(k: list[int]) -> None:
        for r in range(4):
            for c in range(4):
                st[r][c] ^= k[c * 4 + r]

    add(keys[0])
    for rnd in range(1, 11):
        for r in range(4):
            st[r] = [SBOX[x] for x in st[r]]
            st[r] = st[r][r:] + st[r][:r]
        if rnd != 10:
            for c in range(4):
                col = [st[r][c] for r in range(4)]
                for r in range(4):
                    st[r][c] = (
                        _gmul(col[r], 2)
                        ^ _gmul(col[(r + 1) % 4], 3)
                        ^ col[(r + 2) % 4]
                        ^ col[(r + 3) % 4]
                    )
        add(keys[rnd])
    return bytes(st[r][c] for c in range(4) for r in range(4))


def cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    assert len(data) % 16 == 0
    out, prev = b"", iv
    for i in range(0, len(data), 16):
        prev = encrypt_block(
            key, bytes(x ^ y for x, y in zip(data[i : i + 16], prev, strict=True))
        )
        out += prev
    return out


def _dotnet_string(text: str) -> bytes:
    raw = text.encode("utf-8")
    n, prefix = len(raw), bytearray()
    while n >= 0x80:
        prefix.append((n & 0x7F) | 0x80)
        n >>= 7
    prefix.append(n)
    return bytes(prefix) + raw


def plr_plaintext(name: str, difficulty: int = 0, file_type: int = 3) -> bytes:
    """The start of a 1.4 player file: version, metadata, name, difficulty."""
    head = (
        struct.pack("<i", 279)
        + b"relogic"
        + bytes([file_type])
        + struct.pack("<I", 7)  # revision
        + struct.pack("<Q", 0)  # favourite flags
        + _dotnet_string(name)
        + bytes([difficulty])
    )
    # Something after the difficulty byte, as a real file has hair and stats.
    body = head + bytes(range(256)) * 3
    return body + b"\0" * (-len(body) % 16)


def plr_bytes(
    name: str, difficulty: int = 0, key: bytes = PLR_KEY, file_type: int = 3
) -> bytes:
    """A `.plr` holding `name`, encrypted as Terraria encrypts one."""
    return cbc_encrypt(key, key, plr_plaintext(name, difficulty, file_type))
