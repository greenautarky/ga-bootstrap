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

    # The 6 documented bootstrap calls must all have happened, in order.
    flat = [" ".join(c) for c in result.ha_calls]
    sup_idx = next(i for i, s in enumerate(flat) if "supervisor info" in s)
    store_idx = next(i for i, s in enumerate(flat) if "store add" in s)
    registry_idx = next(i for i, s in enumerate(flat) if "docker registries" in s)
    install_idx = next(i for i, s in enumerate(flat) if "addons install" in s)
    start_idx = next(i for i, s in enumerate(flat) if "addons start" in s)
    core_idx = next(i for i, s in enumerate(flat) if "core info" in s)
    assert sup_idx < store_idx < registry_idx < install_idx < start_idx < core_idx


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
    assert result.returncode == 1, (
        f"expected exit 1, got {result.returncode}; stderr={result.stderr}"
    )
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


def test_store_add_real_error_exits_2_after_all_retries(run_bootstrap, fake_ha, bootstrap_env):
    """v1.2.0 retries store-add 6 times before giving up. We queue 6 failures
    AND 6 empty repo-listings (so addon_repo_present always returns false),
    then assert exit 2."""
    for _ in range(6):
        fake_ha.enqueue("store add", stdout="Network error", code=1)
        # Empty repositories list = addon_repo_present returns false
        fake_ha.enqueue(
            "store repositories",
            stdout='{"result":"ok","data":{"repositories":[]}}',
            code=0,
        )
    result = run_bootstrap()
    assert result.returncode == 2, result.stdout + result.stderr
    assert not Path(bootstrap_env["MARKER"]).exists()


def test_missing_ghcr_creds_exits_3(run_bootstrap, tmp_path):
    result = run_bootstrap(GHCR_CREDS_FILE=str(tmp_path / "does-not-exist.json"))
    assert result.returncode == 3
    assert "missing" in result.stdout + result.stderr




def test_creds_fallback_etc_ga(run_bootstrap, tmp_path, fake_ha, bootstrap_env):
    """v1.2.4 fallback: if env-override file is absent but /etc/ga/ exists,
    bootstrap uses the /etc/ga/ path. We can't mount a real /etc/ in a test;
    instead point env-override at a deliberately-missing path AND assert
    that the script's error message says it looked at the fallback chain
    (= proves the chain was traversed before failing)."""
    result = run_bootstrap(GHCR_CREDS_FILE=str(tmp_path / "does-not-exist.json"))
    assert result.returncode == 3
    # Error message must mention the fallback paths the script tried
    err = result.stdout + result.stderr
    assert "/etc/ga/" in err
    assert "/share/ga/" in err
    assert "/mnt/data/" in err


def test_creds_fallback_uses_first_present(run_bootstrap, bootstrap_env, fake_ha):
    """The env-override path is FIRST in the priority chain. When it
    points at a valid file, the script uses it — no fallback needed.
    bootstrap_env's fixture already sets GHCR_CREDS_FILE to a valid
    tmp_path file, so any success-path run proves this works.
    """
    # Force a Step-3 failure via supervisor rejecting the registries call
    # — this only happens if the creds file WAS found and passed to
    # `ha docker registries -f $file`. If the env-override wasn't used,
    # the script would exit 3 with "missing" instead.
    fake_ha.enqueue("docker registries", code=1, stderr="forced fail")
    result = run_bootstrap()
    err = result.stdout + result.stderr
    # Should NOT mention "missing" — the file was found via env
    assert "missing at all candidate paths" not in err
    # Should mention "Supervisor rejected" — meaning we got past Step 3 read
    assert "Supervisor rejected" in err


def test_supervisor_rejects_ghcr_creds_exits_3(run_bootstrap, fake_ha, bootstrap_env):
    fake_ha.enqueue("docker registries", code=1, stderr="403 forbidden")
    result = run_bootstrap()
    assert result.returncode == 3
    assert not Path(bootstrap_env["MARKER"]).exists()


def test_addon_install_fails_exits_4_after_all_retries(run_bootstrap, fake_ha, bootstrap_env):
    """v1.2.0 retries addons-install 6 times before giving up. We queue 6
    install failures AND 6 'no version' addons-info responses (= verify
    fails every time)."""
    for _ in range(6):
        fake_ha.enqueue("addons install", code=1, stderr="resolve_addon failed")
        # version=null means addons_installed returns false
        fake_ha.enqueue(
            "addons info",
            stdout='{"result":"ok","data":{"version":null}}',
            code=0,
        )
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


# --- Step 0a: auto_update=false ---------------------------------------


def test_auto_update_already_false_skips_options_call(run_bootstrap, fake_ha):
    """If `supervisor info` already reports auto_update=false, we MUST
    NOT call `supervisor options` again. Cheap idempotency check."""
    # Replace the default "supervisor info" response so it cleanly reports
    # auto_update already false. The default already does this — make it
    # explicit for the test.
    fake_ha.enqueue(
        "supervisor info",
        stdout='{"result":"ok","data":{"auto_update":false}}',
        code=0,
    )
    result = run_bootstrap()
    assert result.returncode == 0, result.stderr
    options_calls = [c for c in result.ha_calls if "supervisor" in c and "options" in c]
    assert options_calls == [], f"unexpected options call: {options_calls}"


def test_auto_update_true_triggers_options_false_call(run_bootstrap, fake_ha):
    """If Supervisor reports auto_update=true, we MUST call
    `supervisor options --auto-update=false` to flip it."""
    fake_ha.enqueue(
        "supervisor info",
        stdout='{"result":"ok","data":{"auto_update":true}}',
        code=0,
    )
    result = run_bootstrap()
    assert result.returncode == 0, result.stderr
    options_calls = [c for c in result.ha_calls if "supervisor" in c and "options" in c]
    assert len(options_calls) == 1
    assert "--auto-update=false" in options_calls[0]


def test_auto_update_options_failure_is_non_fatal(run_bootstrap, fake_ha, bootstrap_env):
    """If Supervisor rejects the options call, bootstrap MUST continue
    (ga_manager converge will retry). Marker must still be written."""
    fake_ha.enqueue(
        "supervisor info",
        stdout='{"result":"ok","data":{"auto_update":true}}',
        code=0,
    )
    fake_ha.enqueue("supervisor options", code=1, stderr="500 internal")
    result = run_bootstrap()
    assert result.returncode == 0, result.stderr
    assert Path(bootstrap_env["MARKER"]).is_file()
    assert "WARN" in (result.stdout + result.stderr)


# --- Step 6: wait for HA Core ready -----------------------------------


def test_core_ready_immediately_writes_marker(run_bootstrap, bootstrap_env):
    """Default core info returns state=running on the first poll — marker written."""
    result = run_bootstrap()
    assert result.returncode == 0, result.stderr
    assert Path(bootstrap_env["MARKER"]).is_file()
    core_calls = [c for c in result.ha_calls if "core" in c and "info" in c]
    # At least one core info call; in the happy path exactly one (returns running first try).
    assert len(core_calls) >= 1


def test_core_never_ready_still_writes_marker_with_warning(
    run_bootstrap, fake_ha, bootstrap_env
):
    """Step 6 is best-effort — if Core never reports running we log a
    WARN and continue. Marker MUST still be written so future boots
    aren't blocked on the same wait."""
    # Queue 10 non-running responses so the deadline (5 s @ 1 s poll) hits first.
    for _ in range(10):
        fake_ha.enqueue("core info", stdout='{"result":"ok","data":{"state":"starting"}}', code=0)
    result = run_bootstrap()
    assert result.returncode == 0, result.stderr
    assert Path(bootstrap_env["MARKER"]).is_file()
    assert "HA Core never reported running" in (result.stdout + result.stderr)


def test_core_eventually_ready_succeeds(run_bootstrap, fake_ha, bootstrap_env):
    """3 polls returning 'starting' then one returning 'running' — marker written."""
    fake_ha.enqueue("core info", stdout='{"result":"ok","data":{"state":"starting"}}', code=0)
    fake_ha.enqueue("core info", stdout='{"result":"ok","data":{"state":"starting"}}', code=0)
    fake_ha.enqueue("core info", stdout='{"result":"ok","data":{"state":"running"}}', code=0)
    result = run_bootstrap()
    assert result.returncode == 0, result.stderr
    assert Path(bootstrap_env["MARKER"]).is_file()
    core_calls = [c for c in result.ha_calls if "core" in c and "info" in c]
    assert len(core_calls) >= 3
