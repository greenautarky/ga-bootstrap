# Changelog

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
