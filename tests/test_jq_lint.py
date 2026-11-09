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
_REGEX_BUILTIN = re.compile(r'(?<![\w$.])(test|match|capture|scan|splits|sub|gsub)\s*\(')
_SPLIT2 = re.compile(r'(?<![\w$.])split\s*\([^()]*;')
# jq options that consume N following words
_OPT_ARGS = {"--arg": 2, "--argjson": 2, "--slurpfile": 2, "--rawfile": 2,
             "--indent": 1, "-L": 1, "--seq": 0}
_FROM_FILE = {"-f", "--from-file"}
# pinned: real jq programs in the live script (addon_repo_present, step 1a,
# parse check, creds host list + username + password, version, protected, state).
# Update deliberately when a jq call is added or removed.
EXPECTED_LIVE_PROGRAMS = 9
TRUNCATED = "<<shell-quote-truncated-program>>"


def _lex(text: str):
    """Tiny shell lexer. Yields ("op", str) or ("word", segs) where segs is a
    list of (kind, text) with kind in {"s" single-quoted, "d" double-quoted,
    "u" unquoted}. Comments are skipped only where a shell skips them (a '#'
    at the start of a word outside quotes), so '#' inside a quoted jq program
    is NOT treated as a shell comment and an apostrophe in a jq comment
    closes the quote exactly as the real shell would."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t":
            i += 1
        elif c == "\\" and text[i + 1:i + 2] == "\n":
            i += 2
        elif c == "#":
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("$(", i):
            yield ("op", "$(")
            i += 2
        elif c in "\n|;&()<>":
            yield ("op", c)
            i += 1
        else:
            segs, buf = [], ""
            while i < n and text[i] not in " \t\n|;&()<>":
                c = text[i]
                if c == "'" or c == '"':
                    if buf:
                        segs.append(("u", buf))
                        buf = ""
                    e = i + 1
                    while e < n and text[e] != c:
                        e += 2 if (c == '"' and text[e] == "\\") else 1
                    segs.append(("s" if c == "'" else "d", text[i + 1:e]))
                    i = e + 1
                elif c == "\\":
                    buf += text[i:i + 2]
                    i += 2
                elif text.startswith("$(", i):
                    break
                else:
                    buf += c
                    i += 1
            if buf:
                segs.append(("u", buf))
            if segs:
                yield ("word", segs)


def _is_jq(segs) -> bool:
    plain = "".join(t for _, t in segs)
    return plain in ("jq", "$JQ_CLI", "${JQ_CLI}")


_CMD_PREFIX = {"if", "while", "until", "!", "env", "then", "do", "elif", "else", "time", "exec"}
UNLINTED = "<<jq-call-the-lint-cannot-attribute-to-a-program>>"


def jq_programs(text: str) -> list[str]:
    """Program text of each jq invocation. Command position = start of a
    command, after an operator (| ; & && || $( newline) or after
    if/while/until/!/env/then/do/... Programs may be single- or double-quoted.
    A program whose shell quote is closed early (apostrophe in a jq comment)
    gets a TRUNCATED marker. ANY other jq token (not at command position, -f
    file, unquoted/variable program, no program) yields an UNLINTED marker, so
    an unlinted jq call is red, never silently uncounted. Words in comments
    are not tokens at all."""
    toks = list(_lex(text))
    progs = []
    cmd_pos = True
    k = 0
    while k < len(toks):
        kind, val = toks[k]
        if kind == "word" and _is_jq(val):
            if not cmd_pos:
                progs.append(UNLINTED)
                cmd_pos = False
                k += 1
                continue
            k += 1
            skip = 0
            found = False
            while k < len(toks) and toks[k][0] == "word":
                segs = toks[k][1]
                word = "".join(t for _, t in segs)
                if skip:
                    skip -= 1
                elif word in _FROM_FILE:
                    break
                elif word.startswith("-") and len(segs) == 1 and segs[0][0] == "u":
                    skip = _OPT_ARGS.get(word, 0)
                else:
                    if segs[0][0] in ("s", "d") and not (
                            len(segs) == 1 and segs[0][0] == "d"
                            and re.fullmatch(r"\$\{?\w+\}?", segs[0][1])):
                        prog = segs[0][1]
                        if len(segs) > 1 and segs[1][0] == "u" and segs[1][1][:1].isalnum():
                            prog += TRUNCATED
                        progs.append(prog)
                        found = True
                    break
                k += 1
            if not found:
                progs.append(UNLINTED)
            cmd_pos = False
            continue
        cmd_pos = kind == "op" or (
            kind == "word" and len(val) == 1 and val[0][0] == "u" and val[0][1] in _CMD_PREFIX)
        k += 1
    return progs


def violations(prog: str) -> list[str]:
    # drop jq comments (a '#' at line start or after whitespace)
    if prog == UNLINTED:
        return ["jq call the lint cannot attribute to a program (unlinted jq is red)"]
    if TRUNCATED in prog:
        return ["apostrophe closed the shell quote early (jq comment?)"]
    stripped = re.sub(r'(^|\s)#[^\n]*', r'\1', prog)
    return [m.group(0) for m in _REGEX_BUILTIN.finditer(stripped)] + \
           [m.group(0) for m in _SPLIT2.finditer(stripped)]


def _lint_file(path: Path) -> tuple[int, list[str]]:
    progs = jq_programs(path.read_text())
    bad = [v for p in progs for v in violations(p)]
    return len(progs), bad


def test_live_script_has_jq_programs_and_no_regex_builtins():
    n, bad = _lint_file(BOOTSTRAP_SCRIPT)
    assert n == EXPECTED_LIVE_PROGRAMS, (
        f"extraction found {n} jq programs in the live script, expected "
        f"{EXPECTED_LIVE_PROGRAMS}: lint is blind or the script changed")
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


def test_comment_mentions_of_jq_are_not_programs():
    assert jq_programs("# jq 'sub(x)' is bad\necho \"jq 'sub(x)'\"\n") == []


def test_arg_value_is_not_taken_as_program():
    assert jq_programs("jq -e --arg a 'x' '.b'\n") == [".b"]


@pytest.mark.parametrize("text", [
    "if jq -e 'sub(1;2)' f; then :; fi\n",
    "while ! jq -e 'test(\"a\")' f; do :; done\n",
    "x=1 && jq -e 'scan(\"a\")' f\n",
    "env jq -e 'gsub(\"a\";\"b\")' f\n",
    'jq -e "sub(\\"a\\";\\"b\\")" f\n',
    'jq -f prog.jq in.json\n',
    'jq -e "$PROG" in.json\n',
    'xargs jq -e \'.a\'\n',
])
def test_inline_shapes_are_flagged(text):
    progs = jq_programs(text)
    assert progs, text
    assert [v for p in progs for v in violations(p)], (text, progs)
