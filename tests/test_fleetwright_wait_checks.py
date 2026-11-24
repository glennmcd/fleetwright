"""fleetwright-wait-checks: an author's one wait for CI, within the wait cap, in a line or two."""

from __future__ import annotations

import json
from collections.abc import Sequence

import fleetwright_wait_checks as wc
import pytest


class FakeGh:
    """Answers `gh pr checks` with each of `answers` in turn, then the last one again."""

    def __init__(self, *answers: list[dict[str, str]] | str) -> None:
        self.answers = list(answers)
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> tuple[int, str, str]:
        self.calls.append(list(args))
        answer = self.answers[min(len(self.calls), len(self.answers)) - 1]
        if isinstance(answer, str):
            return 1, "", answer
        return 8, json.dumps(answer), ""


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def sleep(self, secs: float) -> None:
        self.slept.append(secs)
        self.now += secs

    def __call__(self) -> float:
        return self.now


def check(name: str, bucket: str, link: str = "") -> dict[str, str]:
    return {"name": name, "bucket": bucket, "link": link}


VERDICT_RED = check("reviewer-verdict", "fail")


def run(gh: FakeGh, wait: float = 100, interval: float = 15) -> tuple[int, list[str], Clock]:
    clock = Clock()
    code, lines = wc.wait(gh, None, wait, interval, clock.sleep, clock)
    return code, lines, clock


def test_passing_checks_end_the_wait_in_one_line() -> None:
    code, lines, _ = run(FakeGh([check("ci", "pass"), check("lint", "skipping"), VERDICT_RED]))
    assert code == wc.PASS
    assert lines == ["ok: 2 checks pass (reviewer-verdict not counted)"]


def test_the_reviewer_verdict_never_counts_as_a_failure() -> None:
    # It stays red until the reviewer posts; an author that waited on it would never stop.
    code, _, _ = run(FakeGh([check("ci", "pass"), VERDICT_RED]))
    assert code == wc.PASS


def test_a_failing_check_prints_only_its_name_and_link() -> None:
    gh = FakeGh([check("ci", "fail", "https://example.test/run/1"), check("lint", "pass")])
    code, lines, _ = run(gh)
    assert code == wc.FAIL
    assert lines == ["FAIL ci https://example.test/run/1"]


def test_it_polls_until_the_checks_settle() -> None:
    gh = FakeGh([check("ci", "pending")], [check("ci", "pending")], [check("ci", "pass")])
    code, _, clock = run(gh)
    assert code == wc.PASS
    assert clock.slept == [15, 15]


def test_it_gives_up_before_the_deadline_and_says_to_run_it_again() -> None:
    code, lines, clock = run(FakeGh([check("ci", "pending"), check("e2e", "pass")]), wait=40)
    assert code == wc.PENDING
    assert lines == ["pending: ci; run fleetwright-wait-checks again"]
    assert clock.now <= 40


def test_no_checks_yet_is_pending_not_passed() -> None:
    code, lines, _ = run(FakeGh([VERDICT_RED]), wait=10)
    assert code == wc.PENDING
    assert "no checks reported yet" in lines[0]


def test_a_gh_failure_is_reported_in_one_line() -> None:
    code, lines, _ = run(FakeGh('no pull requests found for branch "issue/9"'))
    assert code == wc.ERROR
    assert lines == [
        "fleetwright-wait-checks: could not list the checks: "
        'no pull requests found for branch "issue/9"'
    ]


@pytest.mark.parametrize("asked", [100, 600, 10_000])
def test_no_wait_runs_past_the_wait_cap(asked: float, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[float] = []
    monkeypatch.setattr(wc, "wait", lambda gh, t, w, i: (seen.append(w), (wc.PASS, ["ok"]))[1])
    assert wc.main(["--wait", str(asked)], gh=FakeGh([])) == wc.PASS
    assert seen[0] <= 240 < 270


def test_it_asks_gh_for_the_given_branch_or_the_current_one() -> None:
    gh = FakeGh([check("ci", "pass")])
    wc.wait(gh, "issue/12", 10, 5)
    wc.wait(gh, None, 10, 5)
    assert gh.calls == [
        ["pr", "checks", "issue/12", "--json", "name,bucket,link"],
        ["pr", "checks", "--json", "name,bucket,link"],
    ]


def test_the_command_on_the_slots_path_runs_this_script() -> None:
    # The units put the kit's bin/ first on PATH; the prompt names the command, not the script.
    # Executable as git records it, not as the checkout's file system says: Windows has no exec bit.
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    wrapper = root / "bin" / "fleetwright-wait-checks"
    staged = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-s", "bin/fleetwright-wait-checks"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert staged.startswith("100755 ")
    assert "scripts/fleetwright_wait_checks.py" in wrapper.read_text(encoding="utf-8")
