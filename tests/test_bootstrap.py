"""End-to-end behavioural tests for the bootstrap shell script.

Each test sets up the fake `ha` CLI, invokes the real script under a
sandbox env, then asserts on:
  - the script's exit code (= each documented exit code path)
  - the side effect on the marker file
  - the sequence of `ha` calls captured by the shim
"""

from __future__ import annotations

from pathlib import Path


def _called(result, verb_prefix: str) -> bool:
    return any(verb_prefix in " ".join(call) for call in result.ha_calls)


def test_happy_path_writes_marker_and_calls_expected_commands(run_bootstrap, bootstrap_env):
    result = run_bootstrap()
    assert result.returncode == 0, result.stderr
    assert Path(bootstrap_env["MARKER"]).is_file()

    # The 4 documented bootstrap calls must all have happened, in order.
    flat = [" ".join(c) for c in result.ha_calls]
    sup_idx = next(i for i, s in enumerate(flat) if "supervisor info" in s)
    store_idx = next(i for i, s in enumerate(flat) if "store add" in s)
    registry_idx = next(i for i, s in enumerate(flat) if "docker registries" in s)
    install_idx = next(i for i, s in enumerate(flat) if "addons install" in s)
    start_idx = next(i for i, s in enumerate(flat) if "addons start" in s)
    assert sup_idx < store_idx < registry_idx < install_idx < start_idx


def test_marker_present_short_circuits_with_zero_calls(run_bootstrap, bootstrap_env):
    Path(bootstrap_env["MARKER"]).write_text("already done\n")
    result = run_bootstrap()
    assert result.returncode == 0
    assert result.ha_calls == []
    # marker content untouched
    assert Path(bootstrap_env["MARKER"]).read_text() == "already done\n"


def test_supervisor_never_ready_exits_1(run_bootstrap, fake_ha):
    # Queue 10 empty responses — wait deadline (5s with 1s poll) hits before then
    for _ in range(10):
        fake_ha.enqueue("supervisor info", stdout="", code=0)
    result = run_bootstrap()
    assert result.returncode == 1, f"expected exit 1, got {result.returncode}; stderr={result.stderr}"
    assert "Supervisor never reported ok" in result.stdout + result.stderr


def test_supervisor_eventually_ready_succeeds(run_bootstrap, fake_ha, bootstrap_env):
    # First 2 polls return nothing, 3rd returns ok
    fake_ha.enqueue("supervisor info", stdout="", code=0)
    fake_ha.enqueue("supervisor info", stdout="", code=0)
    fake_ha.enqueue("supervisor info", stdout='{"result":"ok"}', code=0)
    result = run_bootstrap()
    assert result.returncode == 0, result.stderr
    assert Path(bootstrap_env["MARKER"]).is_file()


def test_store_add_returning_already_known_is_ok(run_bootstrap, fake_ha, bootstrap_env):
    # Supervisor returns exit 1 with "already" in the body
    fake_ha.enqueue("store add", stdout="Store already added", stderr="", code=1)
    result = run_bootstrap()
    assert result.returncode == 0, result.stderr
    assert Path(bootstrap_env["MARKER"]).is_file()


def test_store_add_real_error_exits_2(run_bootstrap, fake_ha, bootstrap_env):
    fake_ha.enqueue("store add", stdout="Network error", stderr="", code=1)
    result = run_bootstrap()
    assert result.returncode == 2
    assert not Path(bootstrap_env["MARKER"]).exists()


def test_missing_ghcr_creds_exits_3(run_bootstrap, tmp_path):
    result = run_bootstrap(GHCR_CREDS_FILE=str(tmp_path / "does-not-exist.json"))
    assert result.returncode == 3
    assert "missing" in result.stdout + result.stderr


def test_supervisor_rejects_ghcr_creds_exits_3(run_bootstrap, fake_ha, bootstrap_env):
    fake_ha.enqueue("docker registries", code=1, stderr="403 forbidden")
    result = run_bootstrap()
    assert result.returncode == 3
    assert not Path(bootstrap_env["MARKER"]).exists()


def test_addon_install_fails_exits_4(run_bootstrap, fake_ha, bootstrap_env):
    fake_ha.enqueue("addons install", code=1, stderr="resolve_addon failed")
    result = run_bootstrap()
    assert result.returncode == 4
    assert not Path(bootstrap_env["MARKER"]).exists()


def test_addon_start_fails_exits_4(run_bootstrap, fake_ha, bootstrap_env):
    fake_ha.enqueue("addons start", code=1, stderr="start failed")
    result = run_bootstrap()
    assert result.returncode == 4
    assert not Path(bootstrap_env["MARKER"]).exists()


def test_dry_run_skips_marker(run_bootstrap, bootstrap_env):
    result = run_bootstrap(BG_DRY_RUN="1")
    assert result.returncode == 0
    assert not Path(bootstrap_env["MARKER"]).exists()


def test_default_supervisor_slug_passed_to_install(run_bootstrap):
    result = run_bootstrap()
    assert result.returncode == 0
    install_call = next(c for c in result.ha_calls if "install" in c and "addons" in c)
    assert "99f1cad4_ga_manager" in install_call


def test_custom_slug_via_env(run_bootstrap):
    result = run_bootstrap(GA_MANAGER_SLUG="testslug_ga_manager")
    assert result.returncode == 0
    install_call = next(c for c in result.ha_calls if "install" in c and "addons" in c)
    assert "testslug_ga_manager" in install_call
