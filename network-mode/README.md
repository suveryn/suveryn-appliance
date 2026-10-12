# Network mode

An installation runs in one of three network modes (development context §5), and some components
need to know which one: the login page's "Air-gapped" badge may only say so when it is true, and
features that fetch updates or reach a legal source must know whether they can. This folder makes
that one value real: set once at provisioning, readable by any component without a network call or
a database.

| Mode | Meaning |
|---|---|
| `connected` | The appliance may reach the internet |
| `outbound-blocked` | The office's network blocks the appliance's outbound connections; it can still be reached on the local network |
| `air-gapped` | No network path to the internet at all; updates and models arrive on physical media |

## Where the value lives

One file, the only source of the mode:

```
/etc/suveryn/network-mode.env        owner root, 0644 (any component may read it; it holds no secret)
```

```bash
# The installation's network mode, written by suveryn-network-mode at provisioning.
# …
SUVERYN_NETWORK_MODE=air-gapped
```

`/etc/suveryn` is the appliance's configuration folder, next to `/opt/suveryn/bin` (programs) and
`/var/lib/suveryn` (data, e.g. the reader sandbox's folders). No existing configuration file fitted:
the realm build writes a secret, one-time `gateway-oidc.env` to a folder of the installer's choice,
and `suveryn-fetch-models` (in suveryn-core) has no configuration file.

## Setting it (provisioning)

```bash
sudo install -m 0755 network-mode/suveryn-network-mode /opt/suveryn/bin/
sudo /opt/suveryn/bin/suveryn-network-mode set air-gapped     # or connected, outbound-blocked
```

`set` refuses anything but the three values and writes the file atomically, so a reader never sees
half a file. Change the mode the same way, never by editing the file, and restart the services that
load it (below). Only the installer can know the mode: it is **declared, not measured**. Setting
`air-gapped` on a machine that can reach the internet makes every claim built on it false.

## Reading it (any component)

Pick whichever is simplest for the component; both read the same file.

- **As an environment variable.** Add the file to the service's systemd unit, and the process gets
  `SUVERYN_NETWORK_MODE`:

  ```ini
  [Service]
  EnvironmentFile=-/etc/suveryn/network-mode.env
  ```

  The `-` keeps a service starting when the file is missing; the variable is then unset, which
  means unknown. The file is also valid shell (`. /etc/suveryn/network-mode.env`).
- **From the file**, e.g. a component that isn't started by systemd: read the one line
  `SUVERYN_NETWORK_MODE=<mode>`, ignoring blank lines and lines starting with `#`.
- **From a shell script:** `mode=$(/opt/suveryn/bin/suveryn-network-mode get)`; it prints the mode, or
  `unknown` and exits 1.

**Unknown** means the file is missing, or doesn't hold exactly one of the three values (the key
twice counts as unknown, not as the first or last). A component must then take the safe reading
for its purpose: **never claim to be air-gapped, and never rely on outbound access either.** Don't
set `SUVERYN_NETWORK_MODE` anywhere else (a unit's `Environment=`, a gateway env file): one source.

## Who reads it

Nothing yet. Planned:

- **The Keycloak login theme** ([`keycloak/theme`](../keycloak/theme/README.md#the-air-gapped-badge)):
  the "Air-gapped" badge should show only when the mode is `air-gapped`. The theme lives in this
  repository; wiring the badge to this value is a separate step.
- **suveryn-core**, wherever a feature depends on the mode (updates, legal sources). Its gateway can
  load the file with `EnvironmentFile=`.

## Tests

```bash
python3 -m pytest network-mode/tests
```

They run the tool against a file in a temporary folder (`SUVERYN_NETWORK_MODE_FILE` overrides the
path): every mode set and read back, the file's permissions, atomic writes, the file as an
environment file and as shell, refused values, and every kind of unknown.
