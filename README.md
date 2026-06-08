# ga-bootstrap

First-boot orchestrator for GreenAutarky iHost devices.

`ga-bootstrap.service` runs once after `hassio-supervisor.service` is
healthy and does the minimum needed to hand control over to the
`ga_manager` addon — which then converges the rest of the system
(DNS, watchdog, addon options, per-device secrets, dashboard, ...).

## What it does

1. Waits for Supervisor to respond (up to `SUPERVISOR_WAIT_S`, default
   1200 s — eMMC cold-boot can be slow).
2. Adds the public `vibe_addons` add-on store (idempotent).
3. Writes GHCR pull credentials to Supervisor's registries API (needed
   for the private `ga_manager` image).
4. Installs and starts the `ga_manager` add-on.
5. Touches `/mnt/data/.ga-bootstrapped` so the service is a no-op on
   every subsequent boot.

That's the entire scope. Anything else (DNS configuration, watchdog
flags, telegraf tags, bundle-expectation, wizard state, ...) lives in
`ga_manager` converge, never here.

## Installation

This package ships as an OCI artifact at
`ghcr.io/greenautarky/ga-bootstrap:<version>`. The `ha-operating-system`
buildroot pulls it at OS build time and lays the contents into the
rootfs overlay — `/usr/sbin/ga-bootstrap` + the systemd unit at
`/etc/systemd/system/ga-bootstrap.service` (already wired into
`multi-user.target.wants/`).

The pinned version lives in `ha-operating-system/version.yaml` under
`components.ga-bootstrap`.

## Configuration

Defaults are sensible. Override per-device via `/etc/ga/ga-bootstrap.conf`
(sourced at the top of the script):

| Variable | Default | Purpose |
|---|---|---|
| `MARKER` | `/mnt/data/.ga-bootstrapped` | Sentinel file gating re-runs |
| `SUPERVISOR_WAIT_S` | `1200` | Total seconds to wait for Supervisor |
| `SUPERVISOR_POLL_INTERVAL` | `5` | Poll cadence inside the wait |
| `GA_MANAGER_SLUG` | `99f1cad4_ga_manager` | Addon slug to install |
| `VIBE_STORE_URL` | `https://github.com/greenautarky/vibe_addons` | Public addon store |
| `GHCR_CREDS_FILE` | `/etc/ga/ghcr-creds.json` | `{"ghcr.io":{"username":…,"password":…}}` |

Operator can also override via the environment when running the script
directly — env wins over the conf file.

## Why this exists

Before ga-bootstrap, every device's first boot relied on the rack
provisioner pushing a 46-stage pipeline that performed addon install +
DNS + watchdog + dashboard + telegraf-tag + ... in one shot. That's
brittle (network-dependent, hard to retry, racy with Supervisor cold
start) and doesn't survive a factory reset.

Splitting first-boot into ga-bootstrap (= local, OS-baked, runs
unconditionally on first power-on) + ga_manager converge (= addon,
runs every 30 s, idempotent, fleet-manager observable) lets the
provisioner do strictly less and the device do strictly more.

## Testing

```bash
pip install -e .[dev]
pytest tests/
```

Tests exercise the script via a fake `ha` CLI on `$PATH` — no
Supervisor needed.

## Releasing

```bash
# Bump version in pyproject.toml + CHANGELOG.md
git tag v1.0.X
git push origin v1.0.X
```

The release CI builds the OCI artifact at
`ghcr.io/greenautarky/ga-bootstrap:1.0.X` and creates the matching
GitHub Release.
