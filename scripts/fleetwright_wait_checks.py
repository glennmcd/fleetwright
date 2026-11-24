#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# ///
"""Wait for a pull request's checks, within the wait cap, and say in a line or two how they ended.

An author waits on CI before it stops. Left to itself it loaded a tool to watch the checks
(Monitor, about 3,250 tokens for the rest of the session) or scheduled wake-ups. This waits in one
foreground call that ends well within the 270 s wait cap and the Bash tool's default 2-minute
timeout, so the prompt cache stays warm, and prints almost nothing:

    fleetwright-wait-checks            # the pull request of the current branch
    fleetwright-wait-checks issue/12   # or of a branch, or a PR number

Exit codes: 0 every check passed, 1 a check failed (one ``FAIL <name> <link>`` line each), 2 still
pending when the wait ended (run it again), 3 ``gh`` could not list the checks.

``reviewer-verdict`` is not counted: it stays red until the fleet's reviewer posts its verdict, and
that is not the author's to pass. Standard library only; ``gh`` is the kit's shim on the PATH.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable, Sequence

IGNORED = frozenset({"reviewer-verdict"})
PASSED = frozenset({"pass", "skipping"})
FAILED = frozenset({"fail", "cancel"})
DEFAULT_WAIT_SECS = 100
DEFAULT_INTERVAL_SECS = 15

Gh = Callable[[Sequence[str]], tuple[int, str, str]]

PASS, FAIL, PENDING, ERROR = 0, 1, 2, 3


def _gh(args: Sequence[str]) -> tuple[int, str, str]:
    done = subprocess.run(["gh", *args], capture_output=True, text=True, check=False, timeout=60)
    return done.returncode, done.stdout, done.stderr


def read_checks(gh: Gh, target: str | None) -> list[dict[str, str]] | str:
    """The checks that count, or why they could not be read."""
    # gh exits non-zero while a check fails or is pending, yet still prints the JSON.
    target_args = [target] if target else []
    code, out, err = gh(["pr", "checks", *target_args, "--json", "name,bucket,link"])
    try:
        checks = json.loads(out)
    except ValueError:
        lines = (err or out).strip().splitlines()
        return lines[-1] if lines else f"gh exited {code}"
    if not isinstance(checks, list):
        return "gh did not return a list of checks"
    return [c for c in checks if isinstance(c, dict) and c.get("name") not in IGNORED]


def verdict(checks: list[dict[str, str]]) -> int:
    buckets = {c.get("bucket", "") for c in checks}
    if buckets & FAILED:
        return FAIL
    if checks and buckets <= PASSED:
        return PASS
    return PENDING  # none reported yet, or some still running


def wait(
    gh: Gh,
    target: str | None,
    wait_secs: float,
    interval: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[int, list[str]]:
    deadline = clock() + wait_secs
    while True:
        checks = read_checks(gh, target)
        if isinstance(checks, str):
            return ERROR, [f"fleetwright-wait-checks: could not list the checks: {checks}"]
        state = verdict(checks)
        if state == FAIL:
            return FAIL, [
                f"FAIL {c.get('name', '?')} {c.get('link', '')}".rstrip()
                for c in checks
                if c.get("bucket") in FAILED
            ]
        if state == PASS:
            return PASS, [f"ok: {len(checks)} checks pass (reviewer-verdict not counted)"]
        if clock() + interval > deadline:
            running = sorted(c.get("name", "?") for c in checks if c.get("bucket") not in PASSED)
            return PENDING, [
                "pending: "
                + (", ".join(running) if running else "no checks reported yet")
                + "; run fleetwright-wait-checks again"
            ]
        sleep(interval)


def main(argv: list[str] | None = None, gh: Gh = _gh) -> int:
    parser = argparse.ArgumentParser(prog="fleetwright-wait-checks")
    parser.add_argument("target", nargs="?", help="a branch or PR number (default: current branch)")
    parser.add_argument(
        "--wait", type=float, default=DEFAULT_WAIT_SECS, help="most seconds to wait"
    )
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECS)
    args = parser.parse_args(argv)
    # Never past the wait cap, whatever is asked: a longer wait lets the prompt cache expire.
    code, lines = wait(gh, args.target, min(args.wait, 240), max(args.interval, 1))
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
