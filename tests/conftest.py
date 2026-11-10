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

    def addon_missing(self, mode: str) -> None:
        """Make `addons info` answer "does not exist": mode "until_restart"
        (cleared by `ha supervisor restart`) or "always"."""
        self.responses.setdefault("__flags__", {})["addon_missing"] = mode  # type: ignore[index]

    def enqueue(
        self,
        verb_prefix: str,
        *,
        stdout: str = "",
        stderr: str = "",
        code: int = 0,
    ) -> None:
        self.responses.setdefault(verb_prefix, []).append((stdout, stderr, code))

    def set_model(self, *, protected: bool = True, container_sock: bool | None = None) -> None:
        """Initial Supervisor model shared with the docker shim. container_sock
        None = no container yet; True/False = a container that already exists
        with / without the docker.sock mount."""
        container = None if container_sock is None else {"sock": container_sock}
        self.model_path.write_text(  # type: ignore[attr-defined]
            json.dumps({"protected": protected, "container": container}))


@pytest.fixture
def fake_ha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeHA:
    state_path = tmp_path / "fake_ha_state.json"
    log_path = tmp_path / "fake_ha_calls.jsonl"
    seq_path = tmp_path / "call_sequence.log"
    model_path = tmp_path / "model.json"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_path = bin_dir / "ha"

    fake_path.write_text(
        f"""#!{os.path.realpath('/usr/bin/env')} python3
import json, sys
from pathlib import Path
STATE = Path({str(state_path)!r})
LOG = Path({str(log_path)!r})
MODEL = Path({str(model_path)!r})
args = sys.argv[1:]
# Strip the leading `--no-progress` / `--raw-json` flags so the
# matcher key is stable regardless of which the script used.
clean = [a for a in args if not a.startswith('--')]
key = ' '.join(clean[:2])
LOG.write_text((LOG.read_text() if LOG.exists() else '') + json.dumps(args) + '\\n')
with open({str(seq_path)!r}, 'a') as _f:
    _f.write('ha ' + ' '.join(clean) + '\\n')
state = json.loads(STATE.read_text()) if STATE.exists() else {{}}
queue = state.get(key, [])
flags = state.get('__flags__', {{}})
if key == 'supervisor restart':
    # Stateful: a Supervisor restart is what reloads a re-cloned store
    # (a canary, 2026-10-23). Count restarts for at-most-once asserts.
    flags['restarts'] = flags.get('restarts', 0) + 1
    state['__flags__'] = flags
    STATE.write_text(json.dumps(state))
_missing = (
    flags.get('addon_missing') == 'always'
    or (flags.get('addon_missing') == 'until_restart' and not flags.get('restarts'))
)
if key == 'addons info' and _missing:
    # Real Supervisor reply for an add-on no loaded store knows about.
    stdout, stderr, code = (
        '{{"result":"error","message":"Addon ' + clean[2] + ' does not exist"}}', '', 1,
    )
elif queue:
    stdout, stderr, code = queue[0]
    state[key] = queue[1:]
    STATE.write_text(json.dumps(state))
else:
    # Default per verb — must satisfy the script's grep / exit-check.
    if key == 'supervisor restart':
        stdout, stderr, code = '', '', 0
    elif key == 'supervisor info':
        # Provide BOTH the result=ok signal AND the data.auto_update field
        # since step 1a (auto_update=false) also parses this response.
        stdout, stderr, code = '{{"result":"ok","data":{{"auto_update":false}}}}', '', 0
    elif key == 'supervisor options':
        stdout, stderr, code = '', '', 0
    elif key == 'core stats':
        stdout, stderr, code = '{{"result":"ok","data":{{"cpu_percent":1.0}}}}', '', 0
    elif key == 'core info':
        stdout, stderr, code = '{{"result":"ok","data":{{"state":"running"}}}}', '', 0
    elif key == 'info':
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
if code == 0 and key in ('addons start', 'addons restart'):
    # Model: the container's mounts are fixed at CREATION (start of a
    # missing container) or RECREATION (restart); socket iff NOT protected
    # at that moment.
    _m = json.loads(MODEL.read_text())
    if key == 'addons restart' or _m.get('container') is None:
        _m['container'] = {{'sock': not _m['protected']}}
        MODEL.write_text(json.dumps(_m))
sys.stdout.write(stdout)
sys.stderr.write(stderr)
sys.exit(code)
"""
    )
    fake_path.chmod(0o755)

    controller = FakeHA()
    controller.model_path = model_path  # type: ignore[attr-defined]
    model_path.write_text(json.dumps({"protected": True, "container": None}))

    # Sync controller.responses → state file before each run via a
    # marker tied to the controller object id.
    def _flush_state() -> None:
        state_path.write_text(json.dumps(controller.responses))

    controller._flush_state = _flush_state  # type: ignore[attr-defined]
    controller._log_path = log_path  # type: ignore[attr-defined]

    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
    return controller


@dataclass
class FakeDocker:
    """Controller for the shimmed `docker` binary.

    Mirrors `FakeHA` — tests enqueue responses keyed on a 2-word verb
    prefix ("exec hassio_cli"). Each enqueue() consumes a response slot.
    Defaults handle the common `docker exec hassio_cli sh -c '...wget...'`
    path the bootstrap script uses to call the Supervisor security API:
    return ``{"result":"ok","data":{}}`` so the happy path passes.
    """

    responses: dict[str, list[tuple[str, str, int]]] = field(default_factory=dict)
    invocations: list[list[str]] = field(default_factory=list)

    def enqueue(self, verb_prefix: str, *, stdout: str = "",
                stderr: str = "", code: int = 0) -> None:
        self.responses.setdefault(verb_prefix, []).append((stdout, stderr, code))

    def post_accepted_but_ignored(self) -> None:
        """The security POST answers ok but does not change the model."""
        self.responses["__post_noop__"] = True  # type: ignore[assignment]

    def invisible_first(self, n: int) -> None:
        """The first n `container inspect` calls fail (container not yet
        visible right after start)."""
        self.responses["__invisible_first__"] = n  # type: ignore[assignment]

    def only_containers(self, *names: str, all_names: tuple[str, ...] = (
            "app_99f1cad4_ga_manager", "addon_99f1cad4_ga_manager")) -> None:
        """Every container name in all_names NOT in names does not exist
        (`docker container inspect` fails for it)."""
        self.responses["__missing__"] = [n for n in all_names if n not in names]  # type: ignore[assignment]


@pytest.fixture
def fake_docker(tmp_path: Path, fake_ha: FakeHA,
                monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    """Stand-in for the host `docker` binary so the bootstrap's
    `docker exec hassio_cli sh -c '...'` API calls don't hit a real
    daemon. Reuses fake_ha's bin dir so both shims live on the same
    PATH prefix."""
    state_path = tmp_path / "fake_docker_state.json"
    log_path = tmp_path / "fake_docker_calls.jsonl"
    seq_path = tmp_path / "call_sequence.log"
    model_path = tmp_path / "model.json"
    bin_dir = tmp_path / "bin"
    # bin_dir already exists from fake_ha
    fake_path = bin_dir / "docker"
    fake_path.write_text(
        f"""#!{os.path.realpath('/usr/bin/env')} python3
import json, sys
from pathlib import Path
STATE = Path({str(state_path)!r})
LOG = Path({str(log_path)!r})
MODEL = Path({str(model_path)!r})
args = sys.argv[1:]
LOG.write_text((LOG.read_text() if LOG.exists() else '') + json.dumps(args) + '\\n')
# Match on the first two args (typically "exec hassio_cli").
key = ' '.join(args[:2])
with open({str(seq_path)!r}, 'a') as _f:
    _f.write('docker ' + ' '.join(args[:3]) + '\\n')
state = json.loads(STATE.read_text()) if STATE.exists() else {{}}
queue = state.get(key, [])
model = json.loads(MODEL.read_text())
if args[:2] == ['container', 'inspect']:
    _inv = state.get('__invisible_first__', 0)
    if args[-1] in state.get('__missing__', []) or model['container'] is None or _inv > 0:
        if _inv > 0 and args[-1] not in state.get('__missing__', []):
            state['__invisible_first__'] = _inv - 1
            STATE.write_text(json.dumps(state))
        sys.stderr.write('Error: No such container: ' + args[-1])
        sys.exit(1)
if queue:
    stdout, stderr, code = queue[0]
    state[key] = queue[1:]
    STATE.write_text(json.dumps(state))
elif args[:1] == ['inspect']:
    # Mounts as modelled: socket iff the container was created unprotected.
    _sock = model['container'] is not None and model['container']['sock']
    stdout, stderr, code = '/data\\n' + ('/run/docker.sock\\n' if _sock else ''), '', 0
else:
    # Default: assume the call is the Supervisor security API POST and
    # return the canonical success shape. The bootstrap script greps
    # for "result":"ok" to decide whether to proceed.
    stdout, stderr, code = '{{"result":"ok","data":{{}}}}', '', 0
if code == 0 and args[:2] == ['exec', 'hassio_cli'] and '"result":"ok"' in stdout \\
        and any('protected' in a for a in args) and not state.get('__post_noop__'):
    model['protected'] = False
    MODEL.write_text(json.dumps(model))
sys.stdout.write(stdout)
sys.stderr.write(stderr)
sys.exit(code)
"""
    )
    fake_path.chmod(0o755)

    controller = FakeDocker()

    def _flush_state() -> None:
        state_path.write_text(json.dumps(controller.responses))

    controller._flush_state = _flush_state  # type: ignore[attr-defined]
    controller._log_path = log_path  # type: ignore[attr-defined]
    return controller


@pytest.fixture
def bootstrap_env(tmp_path: Path) -> dict[str, str]:
    marker = tmp_path / "marker"
    sl = tmp_path / "fake_sleep"
    sl.write_text(f"#!/bin/sh\necho \"$1\" >> {tmp_path}/sleeps.log\n")
    sl.chmod(0o755)
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
        "SLEEP_CMD": str(tmp_path / "fake_sleep"),
        "GM_READY_STEP_S": "0",
        "GM_POST_STEP_S": "0",
        "GM_START_STEP_S": "0",
        "GM_VISIBLE_STEP_S": "0",
        "GM_RESTART_STEP_S": "0",
        # Short backoff so the retry tests finish in seconds (production
        # is 0 10 30 60 120 240 = ~8 min budget).
        "BACKOFF_SEQUENCE": "0 0 0 0 0 0",
    }


@pytest.fixture
def run_bootstrap(fake_ha: FakeHA, fake_docker: FakeDocker,
                  bootstrap_env: dict[str, str],
                  monkeypatch: pytest.MonkeyPatch):
    """Returns a callable: `run_bootstrap(**overrides) -> CompletedProcess`."""

    def _run(**overrides):
        fake_ha._flush_state()  # type: ignore[attr-defined]
        fake_docker._flush_state()  # type: ignore[attr-defined]
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
        seq = fake_ha._log_path.parent / "call_sequence.log"  # type: ignore[attr-defined]
        result.sequence = seq.read_text().splitlines() if seq.exists() else []  # type: ignore[attr-defined]
        docker_log_path = fake_docker._log_path  # type: ignore[attr-defined]
        result.docker_calls = (  # type: ignore[attr-defined]
            [json.loads(line) for line in docker_log_path.read_text().splitlines()]
            if docker_log_path.exists()
            else []
        )
        return result

    return _run


@pytest.fixture(scope="session", autouse=True)
def _require_jq_without_oniguruma():
    """CI sets GA_REQUIRE_NO_ONIGURUMA=1 and JQ_CLI to a jq built like the
    iHost image's (no oniguruma). Prove that jq really lacks regex, so a CI
    runner jq that quietly has it cannot make the suite green again."""
    if os.environ.get("GA_REQUIRE_NO_ONIGURUMA") != "1":
        return
    jq = os.environ.get("JQ_CLI", "jq")
    r = subprocess.run([jq, "-n", '"a" | sub("a"; "b")'],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 5 and "ONIGURUMA" in r.stderr.upper(), (
        f"{jq} has regex support (rc={r.returncode}): not a faithful device jq")
