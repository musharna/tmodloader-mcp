"""Building the mod, and reading tModLoader's build output honestly.

WHY THIS IS NOT JUST A subprocess CALL

tModLoader refuses to build while the game is open, and says so with
`Mod Build error TML003`. That is a completely recoverable situation with an
obvious fix, and it arrives buried in output that otherwise looks like a
compiler failure. Surfacing it as itself, with the fix, is most of this module's
value — the alternative is reading it as a broken build and going looking for a
syntax error that is not there.

The other half is that "did it succeed" is a QUESTION ABOUT THE OUTPUT, not
about the exit code, which tModLoader does not use reliably here.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from .config import Config

#: The line tModLoader prints when it will not build over a running game.
GAME_OPEN_MARKER = "TML003"

#: The line that means the compiler finished, with its counts.
SUCCESS_MARKER = "Compilation finished with"


@dataclass(frozen=True)
class BuildResult:
    ok: bool
    errors: int
    warnings: int
    game_was_open: bool
    output: str

    @property
    def summary(self) -> str:
        if self.game_was_open:
            return (
                "tModLoader refused to build because the game is open. Close "
                "tModLoader and build again - this is not a compile failure."
            )
        if not self.ok:
            return f"build failed: {self.errors} error(s), {self.warnings} warning(s)"
        return f"Compilation finished with {self.errors} errors and {self.warnings} warnings"


def _counts(line: str) -> tuple[int, int]:
    """Pull the error and warning counts out of the success line."""
    words = line.replace(",", " ").split()
    errors = warnings = 0
    for i, w in enumerate(words):
        if w.startswith("error") and i:
            errors = int(words[i - 1]) if words[i - 1].isdigit() else errors
        if w.startswith("warning") and i:
            warnings = int(words[i - 1]) if words[i - 1].isdigit() else warnings
    return errors, warnings


def interpret(output: str) -> BuildResult:
    """Decide what a build's output means.

    Pure, so the interpretation is testable without building anything - which
    matters because the interesting cases (game open, warnings but no errors)
    are annoying to reproduce on demand.
    """
    game_open = GAME_OPEN_MARKER in output

    for line in output.splitlines():
        if SUCCESS_MARKER in line:
            errors, warnings = _counts(line)
            # A build can print the success line AND still have been refused,
            # if a previous step got that far. The refusal wins: no .tmod was
            # written, so reporting success would be a lie about the artifact.
            return BuildResult(
                ok=errors == 0 and not game_open,
                errors=errors,
                warnings=warnings,
                game_was_open=game_open,
                output=output,
            )

    return BuildResult(
        ok=False, errors=0, warnings=0, game_was_open=game_open, output=output
    )


def build(cfg: Config, *, timeout: float = 600.0) -> BuildResult:
    """Build the configured mod source."""
    try:
        proc = subprocess.run(
            [
                str(cfg.tml_dir / "dotnet" / "dotnet.exe"),
                "tModLoader.dll",
                "-server",
                "-build",
                cfg.mod_source_win,
            ],
            cwd=str(cfg.tml_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            # Success is read from the OUTPUT, not the exit code, which is not
            # reliable here - so a non-zero exit must not raise past the parser.
            check=False,
        )
    except subprocess.TimeoutExpired:
        return BuildResult(
            ok=False,
            errors=0,
            warnings=0,
            game_was_open=False,
            output=f"the build did not finish within {timeout:.0f}s",
        )

    return interpret(proc.stdout + proc.stderr)
