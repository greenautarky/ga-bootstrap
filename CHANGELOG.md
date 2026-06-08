# Changelog

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
