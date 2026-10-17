# Provisioning

`suveryn-provision` installs and wires up every runtime service [suveryn-core](https://github.com/suveryn/suveryn-core)
needs on a fresh Ubuntu 24.04 machine with an NVIDIA GPU, then checks them end to end. It is the
step before an OS image: run it directly on a test box; the image will run it at build or first boot.

```bash
sudo provision/suveryn-provision install --public-url https://suveryn.office.example \
     --network-mode connected [--outbound approved|all] --site on-premise [--models-from /media/suveryn-models]
sudo provision/suveryn-provision verify            # again later, any time
sudo provision/suveryn-provision services start    # start | stop | status
```

Every step is idempotent: running `install` again continues where it stopped or changes nothing.
Secrets are generated on the machine and never printed.

## What it installs

| Service | How | Where |
|---|---|---|
| PostgreSQL 16 + pgvector | Ubuntu's packages; databases `suveryn` and `keycloak`, each with its own role and a random password; extension `vector`. suveryn-core creates its own tables when the gateway starts (`CREATE TABLE IF NOT EXISTS`), so there are no migrations | `127.0.0.1:5432` |
| suveryn-core | At the commit in [`versions.json`](versions.json), `uv sync --locked --all-packages --no-dev`; its embedding and Docling models with `suveryn-fetch-models` | `/opt/suveryn/core`, models in `/var/lib/suveryn/hf` |
| llama-server | Built from llama.cpp at the pinned commit with CUDA; the LLM files from suveryn-core's `models/manifest.json`, each checked against its size and sha256; router presets written from the manifest | `127.0.0.1:8080`, files in `/var/lib/suveryn/models`, `/etc/suveryn/llama-presets.ini` |
| Keycloak 26.8 | The pinned release (sha256 checked); the sūveryn theme ([`keycloak/theme`](../keycloak/theme/README.md)); the production realm ([`keycloak/realm`](../keycloak/realm/README.md): sslRequired all, 180-day events, exact redirect URLs, a new client secret, passwords of 15 characters, and with `first-boot --administrator FILE` the office's administrator from the web setup), imported once and the realm file deleted | `127.0.0.1:8180/idp`, management `127.0.0.1:9000` |
| The reader sandbox | [`reader-sandbox`](../reader-sandbox/README.md): user `suveryn-reader`, its inbox and work folder, the wrapper, and sudo for the gateway to start it as the reader. The gateway's `SUVERYN_EXTRACT_WRAPPER` points at it; `SUVERYN_EXTRACT_UNSANDBOXED` is never set | `/opt/suveryn/bin/suveryn-reader-sandbox`, `/var/lib/suveryn/{inbox,reader}` |
| suveryn-core's gateway | systemd unit, user `suveryn` | `127.0.0.1:8000` |
| Network mode | [`network-mode`](../network-mode/README.md), from `--network-mode` and `--outbound` (asked on a console when omitted). On the appliance (not on a test pod) also its enforcement: ufw for what comes in, the `suveryn-egress` rules and the `suveryn-dns` resolver for what goes out, and `/etc/resolv.conf` pointing at that resolver | `/etc/suveryn/network-mode.env`, `approved-destinations`, `dnsmasq.conf` |
| Cases | `SUVERYN_CASES_DEFAULT_ACCESS` in `gateway.env`, from `--cases-access` (or `CASES_ACCESS` in `first-boot.env`): `members` (the default, fail closed: a new case is its members' only) or `office` (everyone signed in reads a new case; for a notary office, where all staff share the same secrecy). Its manager can restrict a case either way. `SUVERYN_CASES_KEEP`, from `--cases-keep` (or `CASES_KEEP`): how long a closed case is kept before the gateway deletes it with its documents and conversations: `6m`, `1y`, `5y` or `manual` (the default: nothing is chosen for the office; a closed case stays until someone deletes it). `SUVERYN_CONVERSATION_MAX_AGE_DAYS`, from `--conversations-keep` (or `CONVERSATIONS_KEEP`): how long saved conversations are kept, counted from their last change, in cases too: `6m`, `1y`, `5y` (183, 366 and 1827 days) or `manual` (the default: until their owner deletes them). See suveryn-core's `docs/design/cases.md` (suveryn-tracker#10) and the [data lifecycle](https://github.com/suveryn/suveryn-docs/blob/main/administration/data-lifecycle.md) (#31); the web setup and the admin dashboard will ask all three later | `/etc/suveryn/gateway.env` |
| The chat UI | Built from suveryn-core with a pinned Node (`npm ci`, every package by hash) | `/opt/suveryn/ui` |
| The web setup | [`setup`](../setup/README.md) (`image`): the `suveryn-setup` user and units, so that the first start opens `https://<name>.local/` for the office's answers; they run `first-boot` with them | `suveryn-setup.service`, `suveryn-setup-provision.path` |
| TLS and the reverse proxy | [`reverse-proxy`](../reverse-proxy/README.md): nginx on one https origin for the UI, the gateway and Keycloak, strict security headers; the office's certificate (`--tls-cert`, `--tls-key`) or one from a local CA made on the machine | `:443` (and `:80`, redirecting), `/etc/suveryn/tls` |

Configuration lives in `/etc/suveryn`: `gateway.env`, `keycloak.env` and `gateway-oidc.env` (each
0600, owned by the service that reads it), `llama-presets.ini`, `network-mode.env`,
`approved-destinations` and `dnsmasq.conf`. The systemd units are in [`systemd/`](systemd):
`suveryn-llm`, `keycloak`, `suveryn-gateway`, and on the appliance `suveryn-egress` and `suveryn-dns`
(the network mode).

## The GPU licence check

NVIDIA's GeForce driver licence forbids datacenter deployment. With a GeForce card, `--site` must
say where the machine runs, and the answer is printed at the top of the output:

| `--site` | GeForce card | Datacenter-class card |
|---|---|---|
| `on-premise` | Continues: the office's own premises | Continues |
| `datacenter` | **Stops**: a hosted installation needs a datacenter-class GPU | Continues |
| `test` | Continues, marked `!!` as a test or development machine that must never be delivered from a datacenter | Continues |
| not given | **Stops** and asks | Continues |

## Network access while provisioning

Provisioning fetches Ubuntu packages and the pinned downloads, so it runs with network access while
the appliance is staged; `--network-mode` describes the installation once it is in use. For an
air-gapped installation the LLM files can come from physical media with `--models-from <folder>`,
checked against the manifest's sha256 like a download (how models reach an air-gapped office is
still open decision 3 in the development context).

## Verification

`verify` (also the last step of `install`) checks each service as the gateway will use it:

- **PostgreSQL**: the gateway's role reaches its database, and pgvector is installed.
- **llama-server**: the manifest's models are listed, and the default model answers.
- **Keycloak**: ready; through the admin API, realm `suveryn` has sslRequired `all`, theme
  `suveryn` and 180-day events; its discovery, asked as the reverse proxy will ask it (`https` in
  `X-Forwarded-Proto`), gives exactly the issuer the gateway is configured with.
- **The reader sandbox**: the wrapper is set and the bypass is not; then a fictional one-page PDF is
  uploaded as the gateway's user, through the gateway's own document service, its inbox and the
  sandboxed reader, and must come back read (one page), after which it is deleted. It runs on the
  CPU so the running services keep the GPU; the sandbox path is the same.
- **The gateway**: `/health` reports documents, history and sign-in ready (it reaches Keycloak at
  the public https URL, through the proxy).
- **The proxy** ([`reverse-proxy`](../reverse-proxy/README.md)): TLS 1.2/1.3 with a certificate valid
  for the name and TLS 1.1 refused; http redirects to https; the security headers on the UI, the
  API and Keycloak; `/docs`, `/redoc` and `/openapi.json` answer 404.
- **Sign-in and an upload end to end over https**: a temporary Keycloak user signs in through the
  login page as a browser would, uploads the fictional PDF to `/v1/documents`, sees it read, deletes
  it and signs out; the user is then removed.
- **The network mode** is set, and on the appliance the **outgoing-traffic check** passes
  (`suveryn-network-mode check`: the appliance's own rules are loaded, and addresses that are never
  approved can't be reached).

## Without systemd

A container (the RunPod test pod) has no systemd. `services start` then runs the same units with a
small runner: each `ExecStart` as the unit's `User`, with its `EnvironmentFile`s, logging to
`/var/log/suveryn/<unit>.log`, PIDs in `/run/suveryn`. It is for testing only; an appliance runs
systemd, which also applies the units' hardening (`ProtectSystem`, `PrivateTmp`, …) that the runner
doesn't.

## Tests

```bash
python3 -m pytest provision/tests
```

The decisions that need no machine: the GPU licence check, environment files (a value can't add a
line), the units as the runner reads them, the presets from the manifest, the test PDF, the pins.
The installation itself is tested by running `install` and `verify` on a test box.
