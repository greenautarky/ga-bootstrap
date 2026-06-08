"""Shared fixtures for ga-bootstrap shell-script tests.

Strategy: the bootstrap script invokes `ha` (and reads the marker /
config / GHCR-creds files) — both are mockable cheaply.

- `fake_ha`: writes a Python shim onto `tmp_path/bin/ha`, prepends that
  dir to `$PATH`, returns a `FakeHA` controller you set responses on
  *before* running the script. The shim writes a JSONL invocation log
  inside `tmp_path` so each test can assert what arguments the script
  passed.
- `bootstrap_env`: returns the env dict to pass to subprocess.run —
  sets MARKER + GHCR_CREDS_FILE under tmp_path so tests never touch
  real `/mnt/data/` or `/etc/ga/`.
- `run_bootstrap`: helper that calls the real script with the patched
  env + returns (returncode, stdout, stderr, invocation_log).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP_SCRIPT = REPO_ROOT / "src" / "ga-bootstrap" / "usr" / "sbin" / "ga-bootstrap"


@dataclass
class FakeHA:
    """Controller used by tests to define how the shim `ha` reacts.

    Each `responses` entry maps a verb-prefix string ("supervisor info",
    "store add", "addons install", "addons start", "docker registries")
    to a tuple (stdout, stderr, exit_code). Order matters: each verb
    consumes its first match and removes it, so tests can queue several
    responses for the same call (e.g. "supervisor info" returning empty
    twice, then ok). After the queue is exhausted the shim falls back
    to a sensible default per verb.
    """

    responses: dict[str, list[tuple[str, str, int]]] = field(default_factory=dict)
    invocations: list[list[str]] = field(default_factory=list)

    def enqueue(
        self,
        verb_prefix: str,
        *,
        stdout: str = "",
        stderr: str = "",
        code: int = 0,
    ) -> None:
        self.responses.setdefault(verb_prefix, []).append((stdout, stderr, code))


@pytest.fixture
def fake_ha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeHA:
    state_path = tmp_path / "fake_ha_state.json"
    log_path = tmp_path / "fake_ha_calls.jsonl"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_path = bin_dir / "ha"

    fake_path.write_text(
        f"""#!{os.path.realpath('/usr/bin/env')} python3
import json, sys
from pathlib import Path
STATE = Path({str(state_path)!r})
LOG = Path({str(log_path)!r})
args = sys.argv[1:]
# Strip the leading `--no-progress` / `--raw-json` flags so the
# matcher key is stable regardless of which the script used.
clean = [a for a in args if not a.startswith('--')]
key = ' '.join(clean[:2])
LOG.write_text((LOG.read_text() if LOG.exists() else '') + json.dumps(args) + '\\n')
state = json.loads(STATE.read_text()) if STATE.exists() else {{}}
queue = state.get(key, [])
if queue:
    stdout, stderr, code = queue[0]
    state[key] = queue[1:]
    STATE.write_text(json.dumps(state))
else:
    # Default per verb — must satisfy the script's grep / exit-check.
    if key == 'supervisor info':
        # Provide BOTH the result=ok signal AND the data.auto_update field
        # since step 1a (auto_update=false) also parses this response.
        stdout, stderr, code = '{{"result":"ok","data":{{"auto_update":false}}}}', '', 0
    elif key == 'supervisor options':
        stdout, stderr, code = '', '', 0
    elif key == 'core info':
        stdout, stderr, code = '{{"result":"ok","data":{{"state":"running"}}}}', '', 0
    elif key == 'store add':
        stdout, stderr, code = 'Store added', '', 0
    elif key == 'store reload':
        stdout, stderr, code = '', '', 0
    elif key == 'store repositories':
        # The v1.2.0 script verifies addon_repo_present after store add.
        # Default: report the test's VIBE_STORE_URL as present (= happy
        # path). Tests that want to simulate Supervisor's clone race
        # queue a different response.
        import os as _o
        _url = _o.environ.get('VIBE_STORE_URL', 'https://example.invalid/vibe_addons')
        stdout, stderr, code = (
            '{{"result":"ok","data":{{"repositories":[{{"source":"' + _url + '"}}]}}}}',
            '', 0,
        )
    elif key == 'addons install':
        stdout, stderr, code = 'Installed', '', 0
    elif key == 'addons start':
        stdout, stderr, code = 'Started', '', 0
    elif key == 'addons info':
        # The v1.2.0 script verifies addons_installed by checking that
        # version is a non-null string. Default: report installed.
        stdout, stderr, code = (
            '{{"result":"ok","data":{{"version":"0.27.0"}}}}',
            '', 0,
        )
    elif key == 'docker registries':
        stdout, stderr, code = '', '', 0
    else:
        stdout, stderr, code = '', f'fake_ha: no default for {{key!r}}', 0
sys.stdout.write(stdout)
sys.stderr.write(stderr)
sys.exit(code)
"""
    )
    fake_path.chmod(0o755)

    controller = FakeHA()

    # Sync controller.responses → state file before each run via a
    # marker tied to the controller object id.
    def _flush_state() -> None:
        state_path.write_text(json.dumps(controller.responses))

    controller._flush_state = _flush_state  # type: ignore[attr-defined]
    controller._log_path = log_path  # type: ignore[attr-defined]

    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
    return controller


@pytest.fixture
def bootstrap_env(tmp_path: Path) -> dict[str, str]:
    marker = tmp_path / "marker"
    creds_dir = tmp_path / "etc-ga"
    creds_dir.mkdir()
    creds = creds_dir / "ghcr-creds.json"
    creds.write_text(json.dumps({"ghcr.io": {"username": "u", "password": "p"}}))
    return {
        "MARKER": str(marker),
        "SUPERVISOR_WAIT_S": "5",
        "SUPERVISOR_POLL_INTERVAL": "1",
        "HA_CORE_WAIT_S": "5",
        "HA_CORE_POLL_INTERVAL": "1",
        "GA_MANAGER_SLUG": "99f1cad4_ga_manager",
        "VIBE_STORE_URL": "https://example.invalid/vibe_addons",
        "GHCR_CREDS_FILE": str(creds),
        "BG_DRY_RUN": "0",
        # Short backoff so the retry tests finish in seconds (production
        # is 0 10 30 60 120 240 = ~8 min budget).
        "BACKOFF_SEQUENCE": "0 0 0 0 0 0",
    }


@pytest.fixture
def run_bootstrap(fake_ha: FakeHA, bootstrap_env: dict[str, str], monkeypatch: pytest.MonkeyPatch):
    """Returns a callable: `run_bootstrap(**overrides) -> CompletedProcess`."""

    def _run(**overrides):
        fake_ha._flush_state()  # type: ignore[attr-defined]
        env = {**os.environ, **bootstrap_env, **overrides}
        result = subprocess.run(
            ["/bin/sh", str(BOOTSTRAP_SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        # Attach the invocation log so tests can assert what was called.
        log_path = fake_ha._log_path  # type: ignore[attr-defined]
        result.ha_calls = (  # type: ignore[attr-defined]
            [json.loads(line) for line in log_path.read_text().splitlines()]
            if log_path.exists()
            else []
        )
        return result

    return _run
