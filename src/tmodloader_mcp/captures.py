"""Reading a capture back out of the save directory, and refusing the rest.

WHY THIS IS NOT `path.read_bytes()`

`shot` hands back a filesystem path, which is worth nothing to an agent that is
not running on this machine. Returning the bytes is the fix, and doing it
carelessly is worse than the problem: a tool that opens whatever path it is
given and returns the contents is a file-exfiltration primitive with an MCP
interface in front of it.

That is the exact failure this project was built to avoid, arriving from the
other end. OS-level screen capture was tried first and came back with a Teams
inbox and a Discord friend list in frame. The answer was to read the game's own
back buffer, which cannot contain another window BY CONSTRUCTION. A reader that
will open any file on request gives that guarantee away again.

So containment is structural rather than advisory:

- the caller supplies a NAME, never a path;
- the result is RESOLVED and its parent compared to the resolved save directory,
  which is what catches `..`, an absolute path, and a symlink alike. Scanning
  the name for `..` is a blacklist, and blacklists lose to the first spelling
  nobody thought of - a symlink contains no `..` at all;
- and the name must look like a capture, because the save directory also holds
  diag dumps, heartbeats, trigger files and the world itself. Being in the right
  directory is not the same as being the right kind of file.
"""

from __future__ import annotations

import re
from pathlib import Path

#: What `shot` writes: the mod's drop-box name plus the index and region this
#: harness renames it to. Anchored at both ends - an unanchored match would
#: accept `evil-biomancy-shot-001-full.png.exe`.
CAPTURE_NAME = re.compile(r"^biomancy-shot-\d{3}-[a-z]+\.png$")


class CaptureError(RuntimeError):
    """The named capture cannot be served, and the message says which rule."""


def available(save_dir: Path) -> list[str]:
    """Every capture in the save directory, sorted, names only.

    Names rather than paths for the same reason `read` takes one: a path handed
    out is a path that can come back changed.
    """
    if not save_dir.is_dir():
        return []

    return sorted(
        entry.name for entry in save_dir.iterdir() if CAPTURE_NAME.match(entry.name)
    )


def read(save_dir: Path, name: str) -> bytes:
    """The bytes of one capture, or `CaptureError` saying why not.

    The three refusals are deliberately distinct: "that is not a capture name",
    "that is not inside the save directory", and "that capture is not there"
    are different mistakes, and collapsing them into one message makes a typo
    look like a security refusal.
    """
    if not CAPTURE_NAME.match(name):
        raise CaptureError(
            f"{name!r} is not a capture name. Only files this harness wrote - "
            f"biomancy-shot-<index>-<region>.png - can be read back; the save "
            "directory also holds diag dumps, heartbeats and the world."
        )

    root = save_dir.resolve()
    target = (root / name).resolve()

    # Compared AFTER resolving, so `..`, an absolute path and a symlink are all
    # the same case rather than three checks and a fourth nobody wrote.
    if target.parent != root:
        raise CaptureError(
            f"{name!r} resolves to {target}, which is outside {root}. Captures "
            "are served from the save directory only."
        )

    if not target.is_file():
        raise CaptureError(f"there is no capture called {name!r} in {root}")

    return target.read_bytes()
