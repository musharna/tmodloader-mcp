"""What this install actually HAS: worlds, characters, and mods.

WHY THIS EXISTS

`launch` states two preconditions and can check neither of them.

    player  "Must already exist - `-player` does not create one, and a
             duplicate name is kicked."
    world   a WINDOWS path to a `.wld`, which the caller has to already know.

Both are facts about a directory sitting right there. `Players/` and `Worlds/`
are listable, and until now nothing here listed them — so the only way to learn
either was to launch and read the failure, which for a wrong player name is a
kick and for a wrong world path is a readiness timeout blaming the heartbeat.

This is the argument that produced `log_files`, which reads which logs exist
off disk rather than naming them as a constant, applied to the other two
directories the same reasoning covers.

THE THIRD ONE IS THE MOD LIST, AND IT IS THE POINT

`commands` answers `responder: false` when nothing published a command list.
That is one string for three different fixes: the mod is not built, or it is
built and not enabled, or it is enabled and was compiled without the dev
bridge. `session.py` says as much in a message - "check that a world is loaded
and the mod is enabled" - and gave the caller no way to check either.

ENABLED AND BUILT-HERE ARE DIFFERENT FACTS, deliberately reported as two
booleans rather than collapsed into "installed". On the install this was
written against, `enabled.json` lists `Biomancy` and `CheatSheet` while `Mods/`
holds only `Biomancy.tmod` — because a workshop mod is enabled from somewhere
else entirely. A single `installed` flag would call CheatSheet missing, which
is both wrong and the kind of wrong that sends someone rebuilding a mod that
was never the problem.

A CHARACTER'S FILE NAME IS NOT ITS NAME

`launch(player=...)` passes `-player <name>`, and tModLoader matches that
against the name stored INSIDE each `.plr`, not the file's name. They usually
agree and need not: on the install this was measured against,
`Players/Betula.plr` holds a character called `Betula_Calamity`, and the
earlier `players()`, which listed file stems, offered `Betula`, a name `launch`
cannot use. So `players()` opens each file and reports both, plus the
difficulty, because a journey character cannot join a non-journey world and
the other way round.

The `.plr` is AES-128-CBC with key = IV = the UTF-16LE bytes of `h3y_gUyZ`.
The decryption is pure Python below rather than a call out to .NET's `Aes`
through `powershell.exe` (as `session._pid_table` shells out), for three
reasons. This is a read-only listing that the `inventory` tool and the
`start_a_session` prompt run on every call, and a PowerShell start costs about
a second where decrypting 32 blocks per file costs milliseconds. A broken
PowerShell bridge would turn a directory listing into an error. And the code
that runs is the code that is tested, on a Linux runner with no Windows at
all. Only decryption is implemented, and only of a 512-byte prefix, which is
whole blocks and so needs no padding handling. The block cipher is pinned to
the FIPS-197 known-answer vector in the tests, and the whole reader to real
`.plr` files by `tests/live_player_names_check.py`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import windows_path_for


class InventoryError(RuntimeError):
    """Something on disk is here but unreadable, which is not the same as absent."""


@dataclass(frozen=True)
class World:
    """A world, with the spelling `launch` actually wants.

    `path_win` is None off a drive mount rather than guessed - the same rule
    `config.windows_path_for` follows, for the same reason: an invented UNC
    path is an answer nobody measured.
    """

    name: str
    path_win: str | None


@dataclass(frozen=True)
class Player:
    """A character: the file it lives in, and what is inside it.

    `name` is what `launch(player=...)` takes, read from inside the file;
    `file` is the `.plr` stem, and the two can differ. `difficulty` is
    `classic`, `mediumcore`, `hardcore` or `journey`. When the file cannot be
    read or decoded, `name` and `difficulty` are None and `error` says why.
    """

    file: str
    name: str | None
    difficulty: str | None
    error: str | None


@dataclass(frozen=True)
class Mod:
    name: str
    enabled: bool
    built_here: bool


def worlds(save_dir: Path) -> list[World]:
    """Every world in the install, newest first.

    `*.wld` and nothing else. The directory also holds `.twld` companions,
    `.bak`/`.bak2` copies and a `Backups/` folder, and a looser pattern would
    offer `The_Miasma_of_Grief.wld.bak` as something to launch.
    """
    folder = save_dir / "Worlds"
    if not folder.is_dir():
        return []

    found = sorted(folder.glob("*.wld"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [World(name=p.stem, path_win=windows_path_for(p)) for p in found]


def players(save_dir: Path) -> list[Player]:
    """Every character, with the name `launch(player=...)` accepts.

    That name is read from inside each `.plr` (see the module docstring); the
    file stem is reported beside it because the two can differ. A `.plr` that
    cannot be read or decoded is still listed, with `name` None and `error`
    saying why: dropping it would report a character as absent when the truth
    is that this could not read it, which needs a different fix.
    """
    folder = save_dir / "Players"
    if not folder.is_dir():
        return []

    found = [_read_player(p) for p in folder.glob("*.plr")]
    return sorted(found, key=lambda pl: ((pl.name or pl.file).casefold(), pl.file))


def _read_player(path: Path) -> Player:
    try:
        with path.open("rb") as handle:
            head = handle.read(_PLR_PREFIX)
    except OSError as exc:
        return Player(path.stem, None, None, f"cannot read {path.name}: {exc}")
    try:
        name, difficulty = parse_player_header(head)
    except PlayerFileError as exc:
        return Player(path.stem, None, None, f"{path.name}: {exc}")
    return Player(path.stem, name, difficulty, None)


def mods(save_dir: Path) -> list[Mod]:
    """What is enabled, what is built here, and the union of the two.

    A missing `enabled.json` is an empty list: a fresh install has none, and
    that is a real state rather than a fault. A `enabled.json` that EXISTS and
    will not parse is an error, because it means something wrote a mod list
    this cannot read - which needs a human, not a default. That is the same
    line `commands` draws between `responder: false` and a raise.
    """
    folder = save_dir / "Mods"
    if not folder.is_dir():
        return []

    enabled: set[str] = set()
    manifest = folder / "enabled.json"
    if manifest.is_file():
        try:
            parsed = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            # OSError covers a file deleted or locked between the check and
            # the read; either way the answer is the same as unparseable JSON:
            # a mod list exists that this cannot read, which needs a human.
            raise InventoryError(
                f"{manifest} exists and is not valid JSON: {exc}"
            ) from exc
        if not isinstance(parsed, list):
            raise InventoryError(
                f"{manifest} is {type(parsed).__name__}, expected a list"
            )
        enabled = {str(name) for name in parsed}

    built = {p.stem for p in folder.glob("*.tmod")}

    return [
        Mod(name=name, enabled=name in enabled, built_here=name in built)
        for name in sorted(enabled | built)
    ]


# ---- the .plr header -------------------------------------------------------

# Terraria's own constant; the key and the IV are the same 16 bytes.
_PLR_KEY = "h3y_gUyZ".encode("utf-16-le")
_PLR_PREFIX = 512
# The FileMetadata every 1.3+ save carries after its version: seven bytes of
# "relogic", then one byte of file type, where 3 is a player.
_MAGIC = b"relogic"
_TYPE_PLAYER = 3
# version (4) + magic and type (8) + revision (4) + favourite flags (8).
_NAME_AT = 24
_DIFFICULTIES = {0: "classic", 1: "mediumcore", 2: "hardcore", 3: "journey"}


class PlayerFileError(ValueError):
    """The bytes are not a `.plr` header this reader understands."""


def parse_player_header(encrypted: bytes) -> tuple[str, str | None]:
    """(name, difficulty) from the first bytes of an encrypted `.plr`.

    The plaintext is: int32 version; 20 bytes of metadata (the magic and file
    type as one ulong, a uint revision, a ulong of flags); a .NET BinaryWriter
    string (7-bit-encoded length, then UTF-8) holding the name; one byte of
    difficulty. The magic is checked, so a wrong key or a file that is not a
    player is an error rather than a garbage name.

    A difficulty byte outside 0-3 is reported as None rather than guessed: the
    name is still right, and the name is what `launch` needs.
    """
    whole = len(encrypted) - len(encrypted) % 16
    if whole == 0:
        raise PlayerFileError(f"{len(encrypted)} bytes is too short to be a player")
    plain = aes128_cbc_decrypt(_PLR_KEY, _PLR_KEY, encrypted[:whole])

    if len(plain) < _NAME_AT or plain[4:11] != _MAGIC or plain[11] != _TYPE_PLAYER:
        raise PlayerFileError(
            "not a player file: the decrypted header has no relogic/player magic"
        )

    # .NET's 7-bit encoded int: low groups first, high bit means "more". A
    # 32-bit length takes at most five bytes.
    length, at = 0, _NAME_AT
    for shift in range(0, 35, 7):
        if at >= len(plain):
            raise PlayerFileError("truncated inside the name's length")
        byte = plain[at]
        at += 1
        length |= (byte & 0x7F) << shift
        if not byte & 0x80:
            break
    else:
        raise PlayerFileError("the name's length prefix does not terminate")

    if length == 0 or at + length + 1 > len(plain):
        raise PlayerFileError(f"a name of {length} bytes does not fit the header")
    try:
        name = plain[at : at + length].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PlayerFileError(f"the name is not UTF-8: {exc}") from exc

    return name, _DIFFICULTIES.get(plain[at + length])


def aes128_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-128-CBC decryption of whole blocks; no padding is removed."""
    if len(key) != 16 or len(iv) != 16 or len(data) % 16:
        raise ValueError("AES-128-CBC needs a 16-byte key and IV and whole blocks")
    round_keys = _expand_key(key)
    out = bytearray()
    previous = iv
    for i in range(0, len(data), 16):
        block = data[i : i + 16]
        plain = _decrypt_block(round_keys, block)
        out += bytes(a ^ b for a, b in zip(plain, previous, strict=True))
        previous = block
    return bytes(out)


def _xtime(a: int) -> int:
    """Multiply by x (that is, 2) in AES's GF(2^8)."""
    return ((a << 1) ^ (0x11B if a & 0x80 else 0)) & 0xFF


def _gf_tables() -> tuple[list[int], list[int]]:
    """Antilog and log tables over the generator 3."""
    exp, log = [0] * 255, [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x ^= _xtime(x)  # x * 3 = x * 2 + x
    return exp, log


_EXP, _LOG = _gf_tables()


def _mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[(_LOG[a] + _LOG[b]) % 255]


def _sboxes() -> tuple[bytes, bytes]:
    """FIPS-197 5.1.1, computed: the multiplicative inverse, then the affine map."""
    forward = bytearray(256)
    for a in range(256):
        b = _EXP[(255 - _LOG[a]) % 255] if a else 0
        s = b
        for rot in (1, 2, 3, 4):
            s ^= ((b << rot) | (b >> (8 - rot))) & 0xFF
        forward[a] = s ^ 0x63
    inverse = bytearray(256)
    for a, s in enumerate(forward):
        inverse[s] = a
    return bytes(forward), bytes(inverse)


_SBOX, _INV_SBOX = _sboxes()
_MUL9, _MUL11, _MUL13, _MUL14 = (
    bytes(_mul(a, k) for a in range(256)) for k in (9, 11, 13, 14)
)


def _expand_key(key: bytes) -> list[bytes]:
    """FIPS-197 5.2: eleven 16-byte round keys from one 16-byte key."""
    words = [key[i : i + 4] for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        t = words[i - 1]
        if i % 4 == 0:
            t = bytes((_SBOX[t[1]] ^ rcon, _SBOX[t[2]], _SBOX[t[3]], _SBOX[t[0]]))
            rcon = _xtime(rcon)
        words.append(bytes(a ^ b for a, b in zip(words[i - 4], t, strict=True)))
    return [b"".join(words[r * 4 : r * 4 + 4]) for r in range(11)]


def _decrypt_block(round_keys: list[bytes], block: bytes) -> bytes:
    """FIPS-197 5.3 InvCipher; the state is 16 bytes in column order."""
    s = [b ^ k for b, k in zip(block, round_keys[10], strict=True)]
    for r in range(9, -1, -1):
        # InvShiftRows and InvSubBytes together: row `i` rotates right by `i`.
        s = [
            _INV_SBOX[s[((c - row) % 4) * 4 + row]]
            for c in range(4)
            for row in range(4)
        ]
        s = [b ^ k for b, k in zip(s, round_keys[r], strict=True)]
        if r == 0:
            break
        mixed: list[int] = []
        for c in range(0, 16, 4):
            a0, a1, a2, a3 = s[c : c + 4]
            mixed += (
                _MUL14[a0] ^ _MUL11[a1] ^ _MUL13[a2] ^ _MUL9[a3],
                _MUL9[a0] ^ _MUL14[a1] ^ _MUL11[a2] ^ _MUL13[a3],
                _MUL13[a0] ^ _MUL9[a1] ^ _MUL14[a2] ^ _MUL11[a3],
                _MUL11[a0] ^ _MUL13[a1] ^ _MUL9[a2] ^ _MUL14[a3],
            )
        s = mixed
    return bytes(s)
