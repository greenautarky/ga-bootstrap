# Changelog

## 1.2.4 — 2026-06-10

### Added — GHCR creds fallback chain (= post-OTA self-recovery)

After a RAUC slot-switch the new squashfs has no `/etc/ga/ghcr-creds.json`
because that file belonged to the previously-running OS image. Pre-1.2.4
the script exited 3 here; the whole cascade (ga_manager → converge →
custom_components → integration → PIN migration) stayed blocked until
operator intervention.

This release tries 5 candidate paths in priority order:

  1. `$GHCR_CREDS_FILE` env / EnvironmentFile override
  2. `/etc/ga/ghcr-creds.json` — original bake-at-build-time convention
  3. `/share/ga/ghcr-creds.json` — ga_manager `ghcr-creds-write` worker output
  4. `/mnt/data/supervisor/share/ga/ghcr-creds.json` — same path inside Supervisor's share
  5. `/mnt/data/ghcr-creds.json` — provision_test_fixture / operator-friendly fallback

The first existing file wins; if it's the env-default the script behaves
exactly as before. The new paths kick in only when the default is
absent. `/share/ga/` survives OTA because it's on `/mnt/data` (= writable
ext4 partition), and fleet-manager's `cred_autopush.py` (since 0.17.0)
populates it on every newly-registered device — so an OTA'd device gets
self-recovery as soon as ga-bootstrap retries.

Caught on 2026-06-09 BOSv1.2.10 canary: K7's ga-bootstrap.service
exited 3 after the OTA because `/etc/ga/ghcr-creds.json` was gone but
`/share/ga/ghcr-creds.json` was present (= fleet-manager had pushed
creds via `POST /api/devices/.../jobs/ghcr-creds-push`).

### Tests
+2 new tests covering:
- env-override-missing → error message mentions all 5 paths (= chain was traversed)
- env-override-present → file used, no "missing" message

## 1.2.3 — 2026-06-09

### Fixed — store-add idempotency: "already in store" was never honored

The check `grep -q -i 'already' "$store_log"` always returned false
because the previous line `rm -f "$store_log"` deleted the file before
the grep ran. So on a re-bootstrap of a device whose vibe_addons store
was already registered (= classic happens after RAUC OTA + reboot),
`ha store add` would return non-zero with "Can't add ..., already in
the store" on stderr — and the script interpreted this as transient,
retried 6 times, then exit 2.

Cascade impact: ga-bootstrap fail → ga_manager not installed → converge
step 2 (custom_components placement) doesn't run → greenautarky_onboarding
integration doesn't load → no PIN migration. The whole user-facing
stack gets blocked on a fresh fleet upgrade.

Fix: capture `store_output=$(cat "$store_log")` BEFORE the `rm -f`,
then `grep` the variable. ~3-line patch. Verified on K7 canary 2026-06-09
where ga-bootstrap.service had been stuck in "failed" state ~30 min
after the BOSv1.2.9 install.

## 1.2.2 — 2026-06-09

### Added — EnvironmentFile drop-in path
New `EnvironmentFile=-/mnt/data/ga-bootstrap.env` directive in
ga-bootstrap.service lets operators (and on-device tests) override
GHCR_CREDS_FILE, BACKOFF_SEQUENCE etc. without rebuilding the OS
image. The path is on /mnt/data (= writable ext4) because /etc/ is a
RO squashfs on HAOS — see
ha-operating-system memory/todo_ga_bootstrap_creds_path.

Example contents of /mnt/data/ga-bootstrap.env:
```
GHCR_CREDS_FILE=/mnt/data/ghcr-creds.json
BACKOFF_SEQUENCE=0 5 15 30
```

The `-` prefix on EnvironmentFile makes it optional — missing file is
a no-op (= existing behaviour preserved).

Caught by tests/ga_tests/lib/provision_test_fixture.sh on K31
BOSv1.2.7 bench session 2026-06-09. With this fix the fixture can
become a one-liner instead of needing a manual env override per
invocation.

## 1.2.1 — 2026-06-09

### Fixed — systemd dependency name (hassio- → hassos-)
The HAOS upstream systemd unit is named `hassos-supervisor.service`, not `hassio-` (docker container is named with `hassio_`, but the systemd unit driving it is `hassos-`). Tier-2 v1.0.0/1.1.0/1.2.0 had `After=hassio-supervisor.service + Requires=hassio-supervisor.service`, which systemd silently dropped because the named unit did not exist — ga-bootstrap.service never started. Caught on K31 bench BOSv1.2.6 (2026-06-09).

Fix: replace `hassio-supervisor` → `hassos-supervisor` in both After= and Requires= directives.

## 1.2.0 — 2026-06-08

Reintroduces the defensive retry+verify patterns the rootfs ga-bootstrap
had until BOSv1.2.4 — accidentally dropped in the Tier-2 rewrite (v1.0.0).
Build tests `BS-RETRY-01`/`BS-RETRY-02` document why they exist: the
Supervisor's `ha store add` returns 0 once the addition is accepted into
config, but the actual `git clone` happens asynchronously and can fail
with "invalid HEAD" / exit 128 within the first 1-2 minutes. A 0 exit
code is NOT trust-worthy on its own.

### Added — exp-backoff retry around store-add
- `addon_repo_present()` helper that grep'es the URL out of `ha store
  repositories --raw-json` with trailing-slash normalisation.
- Step 2 wraps `ha store add` in a `for pre_sleep in $BACKOFF_SEQUENCE`
  loop, default `0 10 30 60 120 240` (~8 min budget). Each iteration
  requires BOTH a successful `ha store add` AND
  `addon_repo_present` to break. `store reload` is called between
  attempts to nudge Supervisor's scan.
- Exit code 2 only fires after the entire budget is exhausted.

### Added — exp-backoff retry + verify around addon install
- `addons_installed()` helper that checks the addon's reported version
  value is a non-null string in `ha addons info --raw-json` — used to
  guard against the old `grep -q '"version"'` bug where the key was
  present but the value was null.
- Step 4 same `BACKOFF_SEQUENCE` loop around install + verify.
- Exit code 4 only after the entire budget.

### Added — `BACKOFF_SEQUENCE` env override
- Tests run with `BACKOFF_SEQUENCE="0 0 0 0 0 0"` so the suite stays
  under 30 s. Production default unchanged.
- Operators can override on a stubborn device by writing
  `BACKOFF_SEQUENCE="0 60 180 600"` (or similar) into
  `/etc/ga/ga-bootstrap.conf`.

### Test seam additions
- Fake `ha` shim gains defaults for `store repositories` / `store reload`
  / `addons info` (= the new API calls the script makes).
- Two existing failure tests (`test_store_add_real_error_exits_2`,
  `test_addon_install_fails_exits_4`) renamed with `_after_all_retries`
  suffix and now queue 6 failures each — they verify the retry budget
  is honored before fail-exit.

19/19 tests green.

## 1.1.0 — 2026-06-08

Absorbs the OS-rootfs `ga-ha-init.service` so all first-boot
orchestration lives in one place. The matching ha-operating-system
PR deletes `/usr/libexec/ga-ha-init`, its timer companion, and the
old OS-side `/usr/libexec/ga-bootstrap`.

### Added — Step 1a: Supervisor auto_update=false
- Goes via the `ha supervisor options --auto-update=false` API, not
  direct jq on `updater.json` — Supervisor caches `auto_update` in
  memory and rewrites the file on the next refresh, clobbering a
  direct edit (observed on KIB-SON-31 first boot 2026-05-27).
- Idempotent: reads the current value from the `supervisor info` JSON
  we already captured in the wait loop (= one fewer API call) and
  only calls `options` when the flag drifted.
- Non-fatal on API rejection: logs a WARN and continues. `ga_manager`
  converge step 8 keeps enforcing the value every 30 s.

### Added — Step 4a: wait for HA Core ready
- Polls `ha core info` until `data.state == "running"` (default
  cap 600 s, configurable via `HA_CORE_WAIT_S`).
- Best-effort: timeout logs a WARN and continues. The marker file
  is written either way so future boots aren't blocked re-waiting.

### Fixed
- The `supervisor info` parse used `jq -r '.data.auto_update // "missing"'`,
  which jq treats as "missing" for **false** as well as null, so a
  device that already had `auto_update: false` would still receive the
  unnecessary `options --auto-update=false` API call every boot.
  Replaced with `| tostring` and a "null"-treatment for genuinely
  missing fields.

## 1.0.0 — 2026-06-08 (planned)

Initial release. Part of the provisioning v2 architecture (see
[ha-operating-system memory `project_provisioning_v2_plan_2026_06_05`]).

### Behaviour
- Waits for Supervisor (default 1200 s, cold-boot eMMC slack).
- Adds `vibe_addons` public store.
- POSTs GHCR creds into Supervisor's registries API.
- Installs + starts `ga_manager` (slug `99f1cad4_ga_manager`).
- Touches `/mnt/data/.ga-bootstrapped` marker so subsequent boots
  short-circuit at the start.

### Exit codes
- `0` — bootstrap done, or marker already present.
- `1` — Supervisor never came up within the timeout.
- `2` — Adding the vibe_addons store failed.
- `3` — Writing GHCR credentials failed.
- `4` — `ha addons install/start ga_manager` failed.

### Test seam
- Every external binary is invoked via a variable (`HA_CLI=…`) so
  pytest can drop a fake on `$PATH`.
- `BG_DRY_RUN=1` short-circuits the marker write so tests can run
  the full flow without touching the host filesystem outside `tmp_path`.

### Not in scope
- DNS, watchdog flags, addon options — ga_manager converge owns those.
- Per-device secrets (admin password, onboarding PIN, ...) — converge
  reads them from `/share/ga/*` (or `/config/.storage/...` after the
  v1.0.1 secret-refactor) and applies them.
- Telegraf / fluent-bit env files — same.
