"""`inventory.players()` against REAL `.plr` files, checked by .NET itself.

The unit tests decrypt files that `plr_fixture` encrypted, so they prove the
reader agrees with the test's idea of the format. Only real files can show the
format is Terraria's. This reads every `.plr` in a real `Players/` twice:

  1. with `inventory.players()`, the pure-Python reader;
  2. with .NET through `powershell.exe`: `Aes` (CBC, no padding) to decrypt
     and `BinaryReader.ReadString` to read the name - the same classes the
     game writes with, sharing no code with (1).

and fails unless both agree on every file's name and difficulty and neither
reports an error. It also prints which files hold a name other than their
stem, which is the case `players()` used to get wrong.

Run it under WSL with the save directory on argv, or TMODLOADER_SAVE_DIR set:

    uv run python tests/live_player_names_check.py \
        "/mnt/c/Users/<you>/Documents/My Games/Terraria/tModLoader"

No game is started. Not collected by pytest (no `test_` prefix), same as the
other live checks. First observed passing on 2026-11-14 against 16 files from
tModLoader 1.4.4/1.4.5, two of which hold a name other than their stem.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tmodloader_mcp import inventory
from tmodloader_mcp.config import DEFAULT_POWERSHELL, windows_path_for

# Each line out is: index, TAB, base64 of the name's UTF-8, TAB, difficulty
# byte. Base64 because powershell.exe writes stdout in the console code page,
# which would mangle a name like `Betula-瑟银withDarkrai`.
_DOTNET_READER = r"""
$k = [Text.Encoding]::Unicode.GetBytes('h3y_gUyZ')
$aes = [Security.Cryptography.Aes]::Create()
$aes.Key = $k; $aes.IV = $k
$aes.Mode = 'CBC'; $aes.Padding = 'None'
for ($i = 0; $i -lt $paths.Count; $i++) {
    $fs = [IO.File]::OpenRead($paths[$i])
    try {
        $cs = New-Object Security.Cryptography.CryptoStream(
            $fs, $aes.CreateDecryptor(), 'Read')
        $br = New-Object IO.BinaryReader($cs, [Text.Encoding]::UTF8)
        $null = $br.ReadInt32()
        $null = $br.ReadBytes(20)
        $name = $br.ReadString()
        $diff = $br.ReadByte()
        $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($name))
        "$i`t$b64`t$diff"
    } finally { $fs.Dispose() }
}
"""

DIFFICULTIES = {0: "classic", 1: "mediumcore", 2: "hardcore", 3: "journey"}


def dotnet_names(powershell: str, paths: list[str]) -> dict[int, tuple[str, int]]:
    quoted = ",".join("'" + p.replace("'", "''") + "'" for p in paths)
    script = f"$paths = @({quoted})\n{_DOTNET_READER}"
    # -EncodedCommand is UTF-16LE base64, so non-ASCII paths survive the trip
    # across the WSL interop boundary intact.
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    done = subprocess.run(
        [powershell, "-NoProfile", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if done.returncode != 0:
        print(f"FAIL: the .NET reader exited {done.returncode}:\n{done.stderr}")
        sys.exit(1)
    out = {}
    for line in done.stdout.splitlines():
        if not line.strip():
            continue
        index, b64, diff = line.strip().split("\t")
        out[int(index)] = (base64.b64decode(b64).decode("utf-8"), int(diff))
    return out


def main() -> int:
    where = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TMODLOADER_SAVE_DIR")
    if not where:
        print("FAIL: pass the save directory on argv or set TMODLOADER_SAVE_DIR")
        return 1
    save = Path(where)
    powershell = os.environ.get("TMODLOADER_POWERSHELL", DEFAULT_POWERSHELL)

    found = inventory.players(save)
    if not found:
        print(f"FAIL: no .plr files under {save / 'Players'}")
        return 1

    paths = []
    for player in found:
        win = windows_path_for(save / "Players" / f"{player.file}.plr")
        if win is None:
            print(f"FAIL: {save} is not under /mnt/<drive>; .NET cannot open it")
            return 1
        paths.append(win)

    reference = dotnet_names(powershell, paths)

    failures = 0
    for i, player in enumerate(found):
        if player.error is not None:
            print(f"FAIL {player.file}.plr: the reader reported {player.error}")
            failures += 1
            continue
        if i not in reference:
            print(f"FAIL {player.file}.plr: .NET returned nothing for it")
            failures += 1
            continue
        name, diff = reference[i]
        expected = (name, DIFFICULTIES.get(diff))
        got = (player.name, player.difficulty)
        mark = "ok  " if got == expected else "FAIL"
        if got != expected:
            failures += 1
        differs = "  <- not its file name" if player.name != player.file else ""
        print(f"{mark} {player.file}.plr -> {got} (.NET: {expected}){differs}")

    differing = sum(1 for p in found if p.name is not None and p.name != p.file)
    print(
        f"\n{len(found)} files, {failures} failures, "
        f"{differing} holding a name other than their stem"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
