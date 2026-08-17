"""Two clients capturing at the same instant, against the real capture camera.

Nothing in `tests/` can reach this defect. Terraria names the PNG after the
SECOND it started writing it, before the mod is involved at all, so the
collision only exists where Terraria does. Every other test here drives fakes
or bare files and would pass just as happily with the serialisation removed.

Run by hand, on a machine with the game installed:

    .venv/bin/python tests/live_capture_check.py

It launches its own server plus client, joins a second client, fires `capture`
from TWO SEPARATE OS PROCESSES through a barrier, and tears down what it
started. Two processes rather than two threads because the lock being tested is
a filesystem claim between sessions - threads in one interpreter would share
state the real case does not have.

WHAT A PASS MEANS, AND WHY THE CONTROL IS THE POINT. A pass here is worth
nothing on its own: this harness passes trivially if the two captures merely
happen to land in different seconds. Run it against the code from BEFORE
serialisation and watch it fail, which is the only thing that shows it can:

    git worktree add /tmp/pre-fix <commit-before-the-fix>
    PYTHONPATH=/tmp/pre-fix/src .venv/bin/python tests/live_capture_check.py

Measured 2026-08-14 on `b2041a3` (the 0.3.0 release, serialisation absent):
0/6 rounds, both callers reporting one filename every time, and SIX PNGs on
disk for TWELVE requests. Half the pictures were not misattributed - they were
overwritten and lost. The same command on the serialised code: 3/3, six files
for six requests.

THE SECOND IN THE NAME IS NOT WHEN THE FILE WAS WRITTEN. `Capture ...
23_43_59.png` finished writing at 23:44:06 - the name is stamped when the
capture STARTS and the write completes seconds later. Two consequences worth
keeping:

- The stamp mechanism is conservative in the safe direction by a wider margin
  than its design assumed. It records when the REPLY arrived, which is after
  the write finished, which is well after the name was chosen.
- A pre-fix run answers FASTER than a serialised one (1.2s against 8-10s), and
  that is the bug rather than a regression. The loser's `CaptureFind` sees the
  winner's file appear and returns it immediately; the serialised loser waits
  for a picture that is actually its own.
"""

import multiprocessing as mp
import re
import signal
import sys
import time
from pathlib import Path

from tmodloader_mcp import heartbeat
from tmodloader_mcp import session as session_mod
from tmodloader_mcp.config import load
from tmodloader_mcp.session import Session
from tmodloader_mcp.triggers import artifacts_for

#: Three is the fewest that can distinguish a fix from a coincidence: a single
#: round passes by luck often enough to be worthless, and the pre-fix control
#: fails on every one of them.
ROUNDS = 3

PLAYERS = ("n43n", "tst2")
PORT = 7810

#: Generous. A serialised capture is ~10s and a launch is minutes; the guard is
#: here so a wedged run dies loud rather than holding a game open all night.
WALLTIME = 900


def win_to_wsl(win: str) -> Path:
    """`C:\\Users\\...` as this side spells it, so existence can be checked.

    The mod reports the path Terraria used, which is a Windows path even though
    nothing on this side can open one.
    """
    drive = re.match(r"^([A-Za-z]):\\(.*)$", win)
    if not drive:
        return Path(win)
    return Path(f"/mnt/{drive.group(1).lower()}/" + drive.group(2).replace("\\", "/"))


def fire(player: str, barrier, out) -> None:
    """One session's capture, timed, in its own process.

    Builds its own Session rather than inheriting one: two sessions sharing a
    save directory is the case under test, and a forked copy of one session is
    not two sessions.
    """
    sess = Session(cfg=load(), mode="server_client", port=PORT, player=player)
    barrier.wait()
    start = time.time()
    try:
        reply = sess.ask("capture", timeout=60.0)
        text, note, ok, refused = reply.text, reply.note, reply.ok, reply.refused
    except Exception as failure:  # noqa: BLE001 - a raise here is a RESULT
        # Reported, never swallowed - a raise here is a result, and a harness
        # that hid it would report a collision as a timeout.
        text = f"RAISED {type(failure).__name__}: {failure}"
        note, ok, refused = None, False, False

    out.put(
        {
            "player": player,
            "start": start,
            "elapsed": time.time() - start,
            "ok": ok,
            "refused": refused,
            "text": text,
            "note": note,
        }
    )


def _png_of(text: str) -> str | None:
    found = re.search(r"PNG:\s*(.+?)\s*$", text, re.MULTILINE)
    return found.group(1) if found else None


def one_round(number: int, ctx) -> bool:
    barrier = ctx.Barrier(len(PLAYERS))
    out = ctx.Queue()
    procs = [ctx.Process(target=fire, args=(p, barrier, out)) for p in PLAYERS]
    for proc in procs:
        proc.start()
    results = [out.get(timeout=120) for _ in PLAYERS]
    for proc in procs:
        proc.join(timeout=30)

    results.sort(key=lambda r: r["start"])
    print(f"\n===== ROUND {number} =====")
    pngs = {}
    for result in results:
        png = _png_of(result["text"])
        pngs[result["player"]] = png
        print(
            f"  {result['player']}: ok={result['ok']} refused={result['refused']} "
            f"elapsed={result['elapsed']:.2f}s note={result['note']!r}"
        )
        print(f"      png={png}")
        if png:
            print(f"      exists_on_disk={win_to_wsl(png).is_file()}")
        else:
            print(f"      NO PNG IN REPLY: {result['text'][:300]}")

    first, second = (pngs[p] for p in PLAYERS)
    distinct = bool(first) and bool(second) and first != second
    both_exist = (
        distinct and win_to_wsl(first).is_file() and win_to_wsl(second).is_file()
    )
    both_ok = all(r["ok"] and not r["refused"] for r in results)
    skew = (results[1]["start"] - results[0]["start"]) * 1000

    verdict = distinct and both_exist and both_ok
    print(
        f"  --> distinct={distinct} both_exist={both_exist} both_ok={both_ok} "
        f"fire_skew={skew:.0f}ms  ROUND {'PASS' if verdict else 'FAIL'}"
    )
    return verdict


def wait_armed(cfg, deadline: float) -> bool:
    """Both clients loaded, ticking, and listening on their trigger.

    Checked per player rather than directory-wide: an unsuffixed heartbeat is a
    main-menu snapshot every starting client overwrites, and reading it as a
    live client is how a run begins before anyone can answer.
    """
    for player in PLAYERS:
        path = cfg.artifact(artifacts_for(cfg.mod_name, player).heartbeat, server=False)
        while True:
            beat = heartbeat.read(path)
            if beat.present and beat.live and beat.world_ready and beat.armed:
                print(
                    f"{player}: READY  age={beat.age:.1f}s polls={beat.fields.get('polls')}"
                )
                break
            if time.time() > deadline:
                print(
                    f"{player}: NOT READY present={beat.present} live={beat.live} "
                    f"world_ready={beat.world_ready} armed={beat.armed}"
                )
                return False
            time.sleep(2)
    return True


def main() -> int:
    signal.signal(
        signal.SIGALRM,
        lambda *_: (sys.stderr.write("aborting: walltime guard\n"), sys.exit(2)),
    )
    signal.alarm(WALLTIME)

    cfg = load()
    print(f"session module: {session_mod.__file__}")
    sess = session_mod.launch(cfg, "server_client", port=PORT, player=PLAYERS[0])
    try:
        session_mod.join(cfg, sess, PLAYERS[1], timeout=300.0)
        if not wait_armed(cfg, deadline=time.time() + 300):
            return 2

        ctx = mp.get_context("fork")
        verdicts = []
        for number in range(1, ROUNDS + 1):
            verdicts.append(one_round(number, ctx))
            time.sleep(1.0)

        print(f"\n===== {sum(verdicts)}/{ROUNDS} rounds passed =====")
        return 0 if all(verdicts) else 1
    finally:
        print("killed:", session_mod.stop(cfg, sess))


if __name__ == "__main__":
    sys.exit(main())
