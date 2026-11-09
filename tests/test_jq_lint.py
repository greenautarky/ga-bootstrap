"""Lint: device-side jq programs must not use regex builtins.

The shipped iHost jq is built WITHOUT oniguruma, so test/match/capture/scan/
sub/gsub/splits and the two-argument split/2 abort with exit 5 on the device
while working on every dev machine and CI runner (rc7 K31 gate, 2026-11-09).
This reads the LIVE script, never a copy, and FAILS (does not skip) if it
finds no jq program at all. Fixtures: tests/fixtures/jq_lint/{must_fail,must_pass}.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from conftest import BOOTSTRAP_SCRIPT

FIXTURES = Path(__file__).parent / "fixtures" / "jq_lint"
# a jq invocation: "$JQ_CLI" / $JQ_CLI / jq as a command word
_INVOKE = re.compile(r'(?:"\$JQ_CLI"|\$JQ_CLI|(?<![\w./-])jq(?![\w-]))(?=\s)')
_REGEX_BUILTIN = re.compile(r'(?<![\w$.])(test|match|capture|scan|splits|sub|gsub)\s*\(')
_SPLIT2 = re.compile(r'(?<![\w$.])split\s*\([^()]*;')


def jq_programs(text: str) -> list[str]:
    """Single-quoted program text following each jq invocation."""
    progs = []
    for m in _INVOKE.finditer(text):
        q = text.find("'", m.end())
        # the program is the first single-quoted word on the same command
        # (stop at a newline-separated next command that has no quote)
        if q == -1:
            continue
        e = text.find("'", q + 1)
        if e == -1:
            continue
        progs.append(text[q + 1:e])
    return progs


def violations(prog: str) -> list[str]:
    # drop jq comments (a '#' at line start or after whitespace)
    stripped = re.sub(r'(^|\s)#[^\n]*', r'\1', prog)
    return [m.group(0) for m in _REGEX_BUILTIN.finditer(stripped)] + \
           [m.group(0) for m in _SPLIT2.finditer(stripped)]


def _lint_file(path: Path) -> tuple[int, list[str]]:
    progs = jq_programs(path.read_text())
    bad = [v for p in progs for v in violations(p)]
    return len(progs), bad


def test_live_script_has_jq_programs_and_no_regex_builtins():
    n, bad = _lint_file(BOOTSTRAP_SCRIPT)
    assert n >= 5, f"extraction found only {n} jq programs in the live script: lint is blind"
    assert not bad, f"regex builtins in device-side jq (no oniguruma on the image): {bad}"


@pytest.mark.parametrize("fx", sorted((FIXTURES / "must_fail").glob("*.sh")), ids=lambda p: p.name)
def test_lint_flags_must_fail_fixtures(fx):
    n, bad = _lint_file(fx)
    assert n >= 1, "fixture yielded no jq program: extraction broken"
    assert bad, f"{fx.name} must be flagged"


@pytest.mark.parametrize("fx", sorted((FIXTURES / "must_pass").glob("*.sh")), ids=lambda p: p.name)
def test_lint_passes_must_pass_fixtures(fx):
    n, bad = _lint_file(fx)
    assert n >= 1, "fixture yielded no jq program: extraction broken"
    assert not bad, f"{fx.name} must not be flagged, got {bad}"


def test_fixture_sets_are_not_empty():
    assert len(list((FIXTURES / "must_fail").glob("*.sh"))) >= 4
    assert list((FIXTURES / "must_pass").glob("*.sh"))
