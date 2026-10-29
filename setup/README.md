# Web setup (the appliance's side)

The first start of an appliance opens the web setup at `https://<name>.local/`, from another computer
on the office's network (suveryn-tracker#37; design in
[`docs/design/first-boot-setup`](../docs/design/first-boot-setup/README.md)). The screens are
suveryn-core's chat UI (`setup.html`, packages/chat-ui). This folder is what answers them: it checks
and keeps the office's answers, runs provisioning with them, and closes setup once it is done.

| File | What |
|---|---|
| [`suveryn-setup`](suveryn-setup) | `prepare`, `serve`, `provision` (and `watch` for a machine without systemd) |
| [`systemd/`](systemd) | `suveryn-setup.service` (prepare, then serve), `suveryn-setup-provision.path` and `.service` |
| [`nginx-setup.conf`](nginx-setup.conf) | The setup locations: the page at `/` and the API at `/setup/v1/`, from private addresses only |
| [`nginx-setup-site.conf.template`](nginx-setup-site.conf.template) | The site before provisioning: only those locations and the page's files |
| [`tests/`](tests) | Unit tests, and a browser check from Welcome to Complete |

`suveryn-provision image` installs the units and the `suveryn-setup` user; the units run this folder
from the image's checkout at `/opt/suveryn/appliance`. The read-only system (`image/mkosi`) carries
the same: this repository's parts at `/opt/suveryn/appliance`, the units enabled, the user made at
build time (`/etc/passwd` can't be written there), nginx with the header snippets built in and the
office's site on the data partition (`/etc/nginx/sites-enabled/suveryn` links to
`/var/lib/suveryn/nginx/suveryn`; nginx starts once that exists). Not on the installer stick.

## Three parts, so nothing on the network runs as root

| Part | Runs as | Does |
|---|---|---|
| **prepare** (`ExecStartPre=+`) | root | Writes `/var/lib/suveryn/setup/facts.json`: the name, the language chosen at installation, the appliance's identity (UUID and key fingerprint, #35) and the installed models. Makes the appliance's own certificate for `<name>.local` and its address (the local CA's, as provisioning makes it; it is kept when the office chooses "self-signed"), copies it to `/etc/suveryn/tls/setup-cert.pem` for the console (C8), and gives nginx the setup site |
| **serve** | `suveryn-setup`, no capabilities, writes only its own folder | Answers the screens on `127.0.0.1:8090`, through nginx. Checks every answer as the screens do, keeps it in `/var/lib/suveryn/setup/answers/` (0700) and asks for provisioning by writing `answers/start` |
| **provision** (started by the path unit) | root | Reads the answers back as untrusted input, checks them again, runs `suveryn-provision first-boot` with them, reports progress in `progress.json`, and closes setup |

The answers folder holds the administrator's password, a share's password and the office's private
key until setup closes, each 0600, and is then deleted with everything in it. Files are written
through a new file and a rename and read without following links, so neither side can be tricked
into writing or reading elsewhere through a link the other left.

## The API

The contract is at the top of suveryn-core's
[`packages/chat-ui/src/setup/api.ts`](https://github.com/suveryn/suveryn-core/blob/main/packages/chat-ui/src/setup/api.ts);
`tests/test_setup.py` checks that every request in it is answered. Refusals are 422 with
`{"errors": {field: code}}` or `{"error": code}`, and the screens show each code in the office's
language.

| Request | What the appliance checks |
|---|---|
| `GET state` | (The name, the address, gateway and resolver in use, the language, the identity, the models, the USB disks plugged in now, the answers so far without secrets, provisioning's progress) |
| `PUT answers/network` | Connected (approved destinations or everything) or air-gapped |
| `PUT answers/administrator` | A name; an email address; a password of at least 15 characters, not common, not containing the name, the email's local part or the appliance's name: the screen's rules, the same list |
| `PUT answers/certificate` | Self-signed: names `<name>.local` and the address. Own: a PEM certificate (and its chain, in the same file or apart) and an unencrypted PEM private key; the key belongs to the certificate, it hasn't expired, it covers `<name>.local` (a `*.local` wildcard does), and the chain leads to it. With `openssl`, the files in memory only |
| `POST manifest` | A support manifest ([`support/`](../support/README.md)): signed by a trusted portal key, for this appliance, not expired |
| `PUT answers/support` | `{"skipped": true}` |
| `POST backups/test` | The share's address (`\\server\share` or `server:/path`) is on the office's own network (private or link-local) and its server answers on 445 (SMB) or 2049 (NFS). **Reachability only**: the backup service's own test, used after setup, signs in and writes (see [`backup/`](../backup/README.md)) |
| `PUT answers/backups` | A USB disk plugged in now, or a share's address; 14, 30, 90 or 365 days; an age recipient (`age1…`, bech32 checksum checked). The recovery key's private half never reaches the appliance |
| `POST provision` | Every step answered. Starts provisioning, or starts it again after a failure; while it runs or once done, answers can't change |
| `GET provision` | (Progress: `running`, `done` or `failed`, per step) |
| `PUT answers/restore` | A replacement appliance (#56): a USB disk plugged in now, or a share's address (with a user name and password for SMB); a recovery key file holding an `AGE-SECRET-KEY-1…` line. Saves them (the key and password 0600, on the data partition) and asks the root worker to find and check the newest backup there: `checking` |
| `POST restore/start` | The check found a backup (`checked`). Starts the restore; once it runs or is done, answers can't change |
| `GET restore` | (`checking`, `checked` with the backup's date and appliance, `restoring` and `done` per step, or `failed` with a code) |

A request that says where it comes from must come from the appliance's own page (`Origin`,
`Sec-Fetch-Site`), and a body must be JSON (or the manifest's bytes), which a page elsewhere can't
send without the browser asking first. The service logs method, path and status only.

## Restoring a backup instead (#56)

`suveryn-setup provision`, started by the same path unit, sees `answers/restore-check` or
`answers/restore-start` instead of `answers/start`:

- **check**: `suveryn-backup --settings <target> find --json --identity <key>` (the backups that key opens,
  under any appliance's name), then `check <the newest of them> --on-target --json`; the confirm screen
  offers the others' dates (`choices`) and `POST restore/start` takes `{"file": …}`, one of them;
  the result says `checked` with the backup, or `failed` with `wrong_key`, `no_backup`, `unreachable`,
  `damaged`, `newer` (made with a newer PostgreSQL than this appliance's, #57) or `invalid`.
- **restore**: `suveryn-provision restore --settings … --identity … --backup <the file chosen>`, its
  `== step` lines turned into five steps: **database**, **restore**, **models**, **network** (the old
  appliance's network mode and name) and **services**. The key and the share's password are deleted
  afterwards whether it worked or not. On success setup closes as below; the appliance now answers at
  the old name, so the screen points there.

## Provisioning and closing

`suveryn-setup provision` runs:

```bash
suveryn-provision first-boot --public-url https://<name>.local --network-mode <mode> [--outbound <outgoing>] \
    --site on-premise --administrator <file in /run, deleted afterwards> [--models-from <a disk labelled SUVERYN-MODELS>]
```

and turns its `== step` lines into the screen's five steps: **database** (the GPU licence check, the
network mode and its enforcement, PostgreSQL), **sign-in** (Keycloak and the production realm, with
the office's administrator in it as a hash only; see [`keycloak/realm`](../keycloak/realm/README.md)),
**models**, **web** (TLS, nginx, the services, `verify`) and **outgoing** (`suveryn-network-mode
check`: limited, blocked, not limited, or not checked when all outgoing traffic is allowed). A
failure marks its step, the screen offers Try again, and nothing entered is lost. The log is
`/var/log/suveryn/setup-provision.log`, without secrets.

The LLM files come from the signed model bundles the installer copied from the stick
(`/var/lib/suveryn/bundles/model/…`), used where they are once their size and SHA-256 match
suveryn-core's manifest. Without such a bundle, a connected appliance downloads them and the others
need a `SUVERYN-MODELS` disk. Once `verify` has passed, provisioning deletes Keycloak's temporary
administrator (`suveryn-setup` in the master realm) and its password, if the office's administrator
exists (#55). A test machine (`install`, or `first-boot --test-machine`) keeps it.

Once done it keeps what later parts read: `/etc/suveryn/support/manifest.yaml`,
`/etc/suveryn/backups.json` (where, how long, the recipient) and the share's password in
`/etc/suveryn/backups-share.secret` (0600). Then it waits until the browser has shown Complete (at
most 30 minutes) and closes setup:

1. the office's own certificate, if chosen, replaces the local one, and its root certificate joins
   the appliance's trust store (the gateway reaches Keycloak at the public name);
2. `/etc/suveryn/nginx-setup.conf` is deleted and nginx reloaded: `/` is the chat, `/setup/` is 404;
3. `/etc/suveryn/setup-answers.json` keeps what was chosen, without secrets, and
   `/etc/suveryn/setup-complete` is written (the console shows setup as complete);
4. the answers folder is deleted, and the setup units are disabled (on the read-only system, where
   `/etc` can't be written, stopped: their condition on `setup-complete` keeps them from starting again).

On the read-only system the office's own certificate's root, and the local CA, can't join the
system's trust store in `/usr`; the trust bundle on the data partition takes its place with the
services (#39). Provisioning says so in its log.

The certificate the browser accepted during setup stays the same until Complete is on screen: the
self-signed one is the one provisioning keeps, and the office's own goes in only at step 1.

## Testing

```bash
python3 -m pytest setup/tests                       # any machine with openssl; SUVERYN_CORE=<suveryn-core> for the contract checks
sudo python3 setup/tests/browser_check.py           # a disposable machine only: see its header
sudo image/mkosi/test/run-vm-tests                   # step 4b: the web setup on the installed test system (#37 part 6a)
sudo python3 setup/tests/browser_check.py --real    # a FRESH test GPU machine: the real provisioning (#37 part 6b)
```

**The browser check** serves suveryn-core's production setup page with nginx and the appliance's real
headers, runs this service as `suveryn-setup`, and walks every screen in Chromium: a refused key, a
manifest for another appliance, a share outside the office's network, a provisioning failure and Try
again, Complete, and the switch to the real site afterwards (18 checks, no CSP violations). It stands
in for suveryn-provision (`stand_in_provision.py`: the same steps, with the real TLS and nginx site),
the outgoing-traffic check, the identity and the portal's key.

**On the read-only system (6a):** `image/mkosi/test/run-vm-tests` installs the test system from its
stick in a virtual machine with a TPM, and its step 4b boots the installed disk with port 443
forwarded: the same screens, driven in Chromium on the host, against this service under systemd with
its locked-down units, the appliance's real identity, nginx with the site on the data partition, and
the read-only `/etc`. The self-test inside checks the service runs unprivileged, then that setup
closed for good (the units stopped and not started again at the next boots, `/` the chat, `/setup/`
404, the answers and secrets gone). The test system has no sūveryn services or GPU, so provisioning
is the stand-in there; the test image carries the chat UI's build (`image/mkosi/test/make-test-ui`)
and a test portal key (`make-test-keys`).

**The real provisioning (6b), on the Pod:** `browser_check.py --real` on a fresh GPU test machine
after `suveryn-provision image`: the same screens, then the real `suveryn-provision first-boot` as a
test machine (`--site test --test-machine`: no firewall of its own, as `install`), the office's
administrator signing in to the chat through Keycloak, and `suveryn-provision verify`. It refuses a
machine provisioned before: never the one with real documents.

## Open

- **The leaked-password list** (open point, #37): the common-password list is the screen's short
  placeholder, kept identical by a test. The real list goes on the appliance, shared with Keycloak's
  `passwordBlacklist`.
- **The share test** here reaches the server only. The backup service's own test (`suveryn-backup
  request`, `test-share`, used from Administration › Backups) signs in and writes a test file. The web
  setup could use that test later.
