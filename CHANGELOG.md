## 1.2.14 — 2026-11-10

### fix: Supervisor 2026.10 `app_<slug>` containers; docker.sock bound on first start

Before: Supervisor 2026.10 names add-on containers `app_<slug>` (was
`addon_<slug>`). Step 3a inspected `addon_<slug>`, got nothing, returned
`unknown` and only WARNed, so it never restarted. Mounts are fixed at container
creation, and `protected=false` was set only after the first start, so the first
container had no docker.sock; the one restart then hit Supervisor "not ready".
(Proven on a canary, BOSv1.6.0-rc8, Supervisor 2026.10.5.3.)

After:
- `protected=false` is POSTed right after install, BEFORE the first
  `ha addons start`, so the first container already gets the bind.
- `gm_container` resolves `app_<slug>` first, then `addon_<slug>` (older
  Supervisor). Its only call site is `gm_docker_sock_state` (the only former
  `addon_` docker inspect site).
- Step 3a is now a verification after start. Socket absent: `ha addons restart`
  is retried (GM_RESTART_STEP_S=30 steps up to GM_RESTART_MAX_S=600) while
  Supervisor is not ready, then the mounts are re-checked.
- New loud exit codes: 6 = ga_manager container not found under either name (or
  not inspectable); 7 = docker.sock still unbound after restart, or restart never
  succeeded in the bound. If the protected=false POST itself fails, a WARN is
  logged and verification is skipped (unchanged non-fatal behaviour).

## 1.2.13 — 2026-11-09

- A store listing holding several concatenated JSON documents is a retryable WARN, not a fatal exit 5 (re-review of #11).

### fix: `addon_repo_present` works with the image's jq (built without oniguruma)

Before: the URL normalisation used `sub(...)`. The iHost image ships jq 1.7.1
built **without** oniguruma, where every regex builtin exits 5, so the function
never returned true. On a fresh device the store was added, the 409 on re-add
was not recognised as "registered", and bootstrap looped to exit 2 without ever
installing ga_manager (BOSv1.6.0-rc7 K31 gate, Odoo #1610).

After: the normalisation uses `ascii_downcase`, `split`/`join`, `explode`,
`endswith` and slicing only. Same semantics: case, scheme, trailing slashes and
a trailing `.git` are ignored.

Mechanism: `tests/test_jq_lint.py` rejects regex builtins in the live script's jq
programs (must-fail and must-pass fixtures under `tests/fixtures/jq_lint/`, fails
on zero programs found), and CI job `test-jq-no-oniguruma` runs the whole suite
with a jq built `--without-oniguruma`, after proving that jq has no regex.

### fix: jq failures are loud, not "repo not listed" (Odoo #1610 follow-up)

Before: `addon_repo_present` swallowed jq's stderr and exit code, and the caller
treated every non-zero as "not listed". A jq program error (rc 2/3/5, e.g. a jq
without oniguruma) therefore retried silently for ~8 min and ended in a generic
exit 2. A failing `ha` on the left of the pipe was hidden the same way.

After: `ha` and `jq` run as separate steps. `ha` failure logs a WARN with its rc
(transient, retried). The reply is first parse-checked by its own `jq -e 'type'`
call: HTML/502, truncated JSON, text or an empty reply (jq 1.7 answers rc 5 for
these, same as a program error) is a retryable WARN. The real program is total
over JSON types, so valid JSON of an unexpected shape (data as string, top-level
array/number, ...) is also a retryable WARN, and a top-level array is read as the
repo list. Only a failure of the program itself, jq not runnable (126/127) or a
failed mktemp is `ERROR: jq failed in addon_repo_present (rc=N): <stderr>`, and
bootstrap exits with the NEW code **5** at once without retrying (2 stays "store
did not register after all retries"). Tests: stub jq for the program-error path,
the REAL jq (CI also the no-oniguruma 1.7.1) for every transient reply shape.

Lint (`tests/test_jq_lint.py`): the extraction is now a small shell lexer. It
catches an apostrophe in a jq comment (closes the shell quote early) and no
longer takes a `--arg` value as the program; comment lines mentioning "jq" are not
programs; the live script's program count is a pinned constant (9). Command
position now includes if/while/until/!/env/then/do/&&/||, double-quoted programs
are linted, and any jq token the lexer cannot attribute to a linted program
(-f file, variable program, not at command position) is itself a lint failure.

## 1.2.12 — 2026-11-09

### fix: a re-run no longer loops on Supervisor 2026.10's store 409, and step 3a always gets reached (Odoo #1610)

Before: on Supervisor 2026.10 `ha store add` of an already registered store
answers `Error: unexpected server response (status: 409)`. The script decided
"already registered" by grepping that text for the word "already", which is no
longer in it. A boot that was interrupted or slow before step 3a (power cycle,
slow Supervisor) therefore looped for good (6 tries, exit 2, restart after 180 s)
and never reached step 3a. ga_manager stayed `protected=true`, without
docker.sock, and the local Influx credentials were never delivered.

After: the store is "registered" when the repo is **listed** (`ha store
repositories`), whatever the reply wording or exit code. A 409 counts only if
the repo is listed. Any other failure with the repo not listed logs a `WARN`
with the first line of the reply and retries; after 6 attempts it still exits 2.
Step 3a: if Supervisor reports `protected=false` the running container is
checked (`docker inspect addon_<slug>`, mount destination `/run/docker.sock`
or `/var/run/docker.sock`): socket bound -> skip; socket absent (the rc6 end
state: flag flipped, container never recreated) -> restart ga_manager once;
container not inspectable -> WARN, no restart (ga_manager's own startup guard
covers it). `protected=true` flips and restarts as before. At most one restart
per run.

The `store repositories` source match ignores case, scheme, trailing slashes
and a trailing `.git`, and accepts `data` as an object or a plain list.

### fix: host data root `apps/` (Supervisor 2026.10)

Before: the GHCR-credentials fallback globbed only
`/mnt/data/supervisor/addons/data/*_ga_manager/`, a path Supervisor 2026.10 no
longer creates. After: `SUPERVISOR_DATA_ROOTS` (default `.../apps/data` then
`.../addons/data`) is searched in that order.

## 1.2.11 — 2026-11-03

### fix: the restart limit applies again (Odoo #1289)

`StartLimitBurst` / `StartLimitIntervalSec` sat in `[Service]`. Since systemd
230 they are `[Unit]` settings; systemd 257 logs `Unknown key
'StartLimitIntervalSec' in section [Service], ignoring` and keeps the default
10 s window. With `RestartSec=180` the burst of 10 could never be reached, so a
permanently failing bootstrap retried forever instead of stopping after 10
attempts in an hour. Moved both keys to `[Unit]`, values unchanged. Measured
with the unit loaded (not started) on systemd 257: `StartLimitIntervalUSec`
10s before, 1h after.

## 1.2.10 — 2026-10-26

### fix: recover when the GA add-on store is registered but its add-ons never load (Odoo #1337)

Measured 2026-10-23 on a canary (BOSv1.5.2-rc2, first boot): a hard
power cycle during the first clone of `vibe_addons` (slug 99f1cad4) left
`repository.json` zero-length. Supervisor reset and re-cloned the store, but the
add-on list stayed empty — `ha addons info 99f1cad4_ga_manager` answered "does
not exist", `ha store reload` / `ha addons reload` did not help. Bootstrap
retried 6 times (~8 min) and exited 4. `ha supervisor restart` fixed it within
seconds.

The install retry loop now runs a one-time recovery ladder when the add-on
**does not exist** although the store is registered: (1) `ha store reload`,
re-check; (2) if still absent, ONE `ha supervisor restart`, wait until the
Supervisor answers, re-check; then the normal install retries continue. Every
rung logs at WARN with what it observed. At most one Supervisor restart per run;
never when the add-on exists (install failing for another reason keeps the old
behaviour). Verified red-before-green in the fake-`ha` harness.

## 1.2.9 — 2026-07-30

### fix: lift a blocking Supervisor job condition instead of retrying it for 8 minutes

`ha store add` can fail with `'StoreManager.add_repository' blocked from
execution, supervisor needs to be updated first`. That is a **job condition**,
not the transient supervisor-git race the retry loop was built for — retrying it
six times over eight minutes changes nothing, and then bootstrap gives up.

Measured on K31 2026-07-30: the OS build was baking supervisor 2025.11.4.5 while
devices poll 2025.11.4.6, so `supervisor_updated` tripped on **every freshly
flashed device**. No store, no add-ons, no ga_manager, no provisioning — and no
error anywhere except this log. The device boots, answers on serial, has a
correct keyring, and is inert.

ha-operating-system#295 removes that cause. This removes the dependency on the
cause staying fixed: a device that cannot register its store cannot be repaired
remotely, so that must not hinge on a version comparison being perfect.

The script now recognises the message, lifts the condition with
`ha jobs options --ignore-conditions supervisor_updated`, and retries the store
add immediately rather than burning a backoff step.

Deliberate trade-off: `ignore-conditions` is persistent Supervisor state, so the
condition stays ignored afterwards. That is the cheaper failure. Explicitly
**not** `supervisor update`, which triggers the armv7 Core image flip — pinned
by its own test so nobody "simplifies" it later.

Verified red-before-green: with the script reverted the new test fails on
"did not lift the blocking job condition".

# Changelog

## 1.2.8 — 2026-06-24

### Fixed — force `protected=false` on the ga_manager addon post-install

After installing/priming the ga_manager addon, ga-bootstrap now POSTs
`{protected: false}` to the Supervisor security API for the addon. This
enables ga_manager's `docker_api` self-update path out of the box —
pre-1.2.8 fresh-flash devices needed a manual operator toggle, the root
cause of the K49 / K31 "Supervisor docker.sock missing" incident
(2026-06-21). Idempotent; runs once post-install.

> Released 2026-06-24: the code landed on main as 1.2.8 (pyproject) but
> the `v1.2.8` tag / OCI artifact was never cut, so the `version.yaml`
> pin in ha-operating-system could not be vendored at build time. This
> release publishes it.

## 1.2.7 — 2026-06-11

### Added — Auto-retry on transient failures (creds-missing race)

`ga-bootstrap.service` unit now declares:

```
Restart=on-failure
RestartSec=180
StartLimitBurst=10
StartLimitIntervalSec=3600
```

So when ga-bootstrap exits 3 because creds aren't yet on the device,
systemd auto-retries every 3 min. Combined with fleet-manager's
`POST /api/devices/{id}/jobs/ghcr-creds-push` (= 0.27.1 worker writes
to /data/) and the v1.2.5 glob fallback chain (= reads from /data/),
this means OTA-recovered devices SELF-HEAL without operator action.

Cap: 10 retries within 1 hour (= 30 min sustained loop). After that
systemd gives up + operator must `systemctl reset-failed && start`.
This prevents hiding a permanent problem behind infinite retries.

`ConditionPathExists=!/mnt/data/.ga-bootstrapped` (= pre-existing)
guarantees that once bootstrap succeeds + writes the marker, no
further retries fire on subsequent reboots.

Closes the manual-recovery step that K7/K0/K17 BOSv1.2.12 cascade
2026-06-10 needed.

## 1.2.6 — 2026-06-10

### Fixed — Supervisor CLI: `ha docker registries -f` not a valid flag

`-f <file>` was removed from the `ha` CLI in a Supervisor pre-v1.2 bump.
Live error on K7 + K31 BOSv1.2.11 reflash:

  ERROR: Supervisor rejected GHCR creds

caused by:

  Error: unknown shorthand flag: 'f' in -f

So the entire creds-registration step has been silently broken for any
device that doesn't have a baked-in `/etc/ga/ghcr-creds.json` that
matches `ha`'s expectations — which means the OTA-recovery path that
1.2.4 + 1.2.5 introduced never actually worked end-to-end.

This release parses the JSON ourselves with `jq` and registers each
host via the documented `ha docker registries add <host> --username U
--password P` flow. Idempotent (Supervisor accepts re-adds), per-host
(one bad entry doesn't abort all).

Caught 2026-06-10 on K31 fresh-flash + K7 canary OTA of BOSv1.2.11.

## 1.2.5 — 2026-06-10

### Security — retire /share/ from the GHCR creds fallback chain

ga-bootstrap 1.2.4 introduced a 5-path fallback for finding GHCR creds
post-OTA. One of those paths was /share/ga/ghcr-creds.json — the place
ga_manager 0.16..0.27.0 wrote them. That path is **addon-readable by
every addon declaring `map: [share:rw]`** (= Z2M, Mosquitto, many
community ones), so as soon as we let customers install arbitrary
HACS-style addons it becomes an exfil vector.

1.2.5 drops /share/ entirely from the fallback. The new chain is:

  1. $GHCR_CREDS_FILE env / EnvironmentFile override
  2. /etc/ga/ghcr-creds.json (= OS-bake-time path)
  3. /mnt/data/ghcr-creds.json (= operator / test-fixture path, root-only)
  4. /mnt/data/supervisor/addons/data/*_ga_manager/ghcr-creds.json
     (GLOB — addon-private path written by ga_manager 0.27.1+)

The glob in path 4 tolerates slug renames: today the addon is
`99f1cad4_ga_manager`, but a future rebuild under a different
installation slug would still match. Addon-private dirs are NOT
readable by other addons regardless of their `map:` declarations.

Sister move to greenautarky-onboarding v1.0.1 + v1.0.3 (= console-login
secret + onboarding PIN out of /share/ + /config/ top-level).

### Companion changes
- ga_manager 0.27.1: `ghcr-creds-write` worker default path moves from
  `/share/ga/ghcr-creds.json` to `/data/ghcr-creds.json`. Worker also
  auto-deletes the legacy /share/ file when it writes the new one.
- ha-operating-system BOSv1.2.11 pins both above.

### Tests
+1 in tests/test_bootstrap.py covering the glob path is mentioned in
the error message + /share/ga/ is NOT (= retired). Plus existing
fallback test updated for the new path enumeration.

22/22 PASS.

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
