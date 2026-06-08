# Security model — ga-bootstrap

## What ga-bootstrap handles

A single fleet-wide secret: the **GHCR pull credentials** used by
Supervisor to fetch the private `ga_manager` add-on image.

| Secret | Stored at | Notes |
|---|---|---|
| GHCR creds (`{"ghcr.io":{"username":…,"password":…}}`) | `/etc/ga/ghcr-creds.json` (0600, root-owned) | Baked into the OS rootfs at build time. Identical across the fleet. Rotatable by re-baking + OTA. |

The file is mounted only into the rootfs filesystem — it is **not**
in `/share/` (which would expose it to every customer-installed
addon), and **not** in `/config/` (which is HA Core but addon-mountable
on edge cases). `/etc/ga/` is system-only.

After bootstrap consumes it, the credentials live inside Supervisor's
internal registries auth (also not readable by addons). The file on
disk stays so a re-bootstrap (e.g. after factory reset) can re-prime
Supervisor without re-fetching.

## What ga-bootstrap explicitly does NOT handle

Per-device secrets (admin password, onboarding PIN, Tailscale authkey,
device label) are written by the provisioner at rack-stamping time and
consumed by `ga_manager` converge — never by ga-bootstrap. This
preserves a clean responsibility split:

- ga-bootstrap = fleet-wide bring-up.
- ga_manager converge = per-device personalisation.

## Threat model

- **Customer-installed addon trying to exfiltrate the GHCR PAT** —
  defended: `/etc/ga/ghcr-creds.json` is not in `/share/` or `/config/`,
  the only paths Supervisor mounts into addons.
- **PAT leaked by a misbehaving fleet** — defended *operationally*: PAT
  scope is `read:packages` only; rotation = re-bake OS + OTA, no per-
  device change.
- **Stale marker preventing re-bootstrap after factory reset** —
  defended: the marker lives in `/mnt/data/`, which is wiped by any
  ga-flasher re-provisioning pass. A pure HA factory reset does not
  clear `/mnt/data/` but also does not need a re-bootstrap (ga_manager
  stays installed).
- **Supervisor not actually ready when `ha` returns ok** — out of scope;
  Supervisor's own `info --raw-json` is the source of truth.

## Not defended against

- Compromise of the bake server (the OS image carries the PAT). If
  the bake host is owned, all images carrying that PAT are
  compromised. Mitigated by PAT rotation cadence.
- Adversary on the device with root — they can already do anything;
  the marker file + the GHCR-creds file are root-readable by design.
