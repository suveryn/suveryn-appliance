# Appliance architecture: read-only system, encrypted data, install from USB, whole-image updates

**Status: approved 14 October 2026, with the decisions at the end.** Tracker: #39 (this design), with #34 (disk
encryption), #35 (identity, "system intact"), #36 (signed bundles), #37 (installer and setup), #40
(updates) and #43 (remote support). Background: development context §5.8 and §8.

## In short

Today the appliance is an ordinary Ubuntu installation: the image is written to the disk with `dd`,
and the first boot changes it in place. This design turns it into an appliance in the stricter
sense, like the systems in network equipment and phones:

1. **The system is read-only and verified.** Everything sūveryn runs (Ubuntu, the NVIDIA libraries,
   PostgreSQL, Keycloak, llama-server, suveryn-core) sits in one image that can't be changed on the
   appliance. Every block is checked against a fingerprint as it is read, so a changed file is
   refused, not run.
2. **Everything that belongs to the office sits on one encrypted data partition:** documents,
   database, settings, certificates, models, logs, the appliance's identity. It unlocks
   automatically with the machine's TPM, and with a recovery key when that fails.
3. **The USB stick boots the same system in "installer mode".** The console installer (#37) copies
   the system onto the disk, creates the encrypted data partition and the recovery
   key, and copies the signed models from the stick. The first start makes the identity.
4. **An update replaces the whole system.** There are two system slots, A and B. An update is
   written to the slot that isn't running; the appliance restarts into it, checks itself, and goes
   back to the previous slot on its own if the new one doesn't come up healthy.
5. **Remote support only when connected (#43).** The office's administrator starts a time-limited
   support session; the appliance opens the connection outwards, so nothing on the office network
   is opened inwards. An air-gapped appliance never has remote support: there the administrator
   exports a diagnostics file instead.

What this gives: the office's data is unreadable on a stolen or returned disk (#34); the system the
office runs is exactly the one Sūveryn Labs released, and the appliance can say so in each check-in
(#35, "system intact"); updates can't leave an office with a half-updated, broken appliance (#40).

What it costs: Ubuntu's own automatic security updates (apt) can no longer run, because nothing can
change the system on the appliance. Security fixes reach offices only as sūveryn updates. That is a
commitment for Sūveryn Labs (decision D3 below).

## The disk

| # | Partition | Size | Contents | Encrypted | Changes |
|---|---|---|---|---|---|
| 1 | EFI system partition (FAT32) | 1 GiB | The boot loader (systemd-boot) and one boot file per slot (a unified kernel image: kernel, initrd and the system's fingerprint in one file). Nothing secret | No (firmware must read it) | Only by updates |
| 2 | System A | 32 GiB | The read-only system (EROFS) | No, it holds nothing of the office's | Only by updates |
| 3 | System A verity | ~260 MiB | The fingerprints of every block of system A (dm-verity) | No | Only by updates |
| 4 | System B | 32 GiB | The other slot, for the next update | No | Only by updates |
| 5 | System B verity | ~260 MiB | Its fingerprints | No | Only by updates |
| 6 | Data | The rest | `/var`: database, documents, settings, certificates, models, logs, identity | **Yes**, LUKS2 (#34) | Normal use |

Notes:

- **32 GiB per slot** leaves room for growth; step 1 measures the real system and sets the size
  from it. On a 2 TB disk two slots take about 3%.
- **No swap.** Answers and document text would otherwise be paged to disk; the hardware profiles
  have enough memory (64 GB Standard). If memory runs out, a compressed swap in memory (zram) is the
  fallback, never swap on disk.
- **The data partition is mounted `noexec,nosuid,nodev`.** Nothing on it can be run as a program.
  (A script handed to an interpreter that is in the system still runs; the limit is stated, not
  hidden.)
- **`/tmp` is in memory.** Temporary files never reach a disk.

### Where the office's settings live

`/etc` comes from the read-only system and changes only with updates. A short, listed set of paths
in it are links into the data partition, because they belong to this office:

| Path | Points to | What |
|---|---|---|
| `/etc/suveryn` | `/var/lib/suveryn/etc` | The network mode, approved destinations, service settings and secrets |
| `/etc/nginx/sites-enabled/suveryn` | `/var/lib/suveryn/nginx/suveryn` | The office's site (its name) |
| `/etc/hostname`, `/etc/hosts` | `/var/lib/suveryn/host/…` | The appliance's name (set in the installer) |
| `/etc/localtime` | `/var/lib/suveryn/host/localtime` | The time zone (set in the installer, C1) |
| `/etc/default/keyboard` | `/var/lib/suveryn/host/keyboard` | The console keyboard layout (set in the installer, C1) |
| `/etc/machine-id` | `/var/lib/suveryn/host/machine-id`, mounted over it in the initrd (systemd won't follow a link for this one) | Unique per appliance |
| `/etc/systemd/network` | `/var/lib/suveryn/network` (the whole folder is a link) | Fixed address or DHCP (#37, C4) |
| `/etc/postgresql` | `/var/lib/suveryn/postgresql-etc` | PostgreSQL's cluster configuration, made with the cluster at first boot (#47) |

Everything else stays where the image puts it. A test that nothing outside this list is written at
first boot is still to do (stage 4, below).

## The boot, and what "verified" means

1. The firmware starts systemd-boot from the EFI partition, which starts the newest healthy slot's
   unified kernel image.
2. That image carries the slot's **root hash**: one fingerprint that covers every block of the
   system. The kernel checks each block against it as it is read (dm-verity). A changed block is a
   read error, never wrong data.
3. The data partition is unlocked by the TPM (PCR 7, as decided in #34 and #37), or with the
   recovery key on the console (C8b, 5 attempts).
4. sūveryn starts. The first time, that means the web setup (#37); after that, the services.

**What it protects, and what not.** Without Secure Boot (not required: the hardware is the office's)
the boot file itself isn't checked by the firmware. Someone with the machine in their hands could
replace both the system and its boot file. That is the "light protection" #35 accepts:

- The appliance **reports** the root hash it booted, and the boot measurement in the TPM (PCR 11),
  in every check-in. Sūveryn Labs publishes the root hash of every release, so the portal can tell
  "system intact: yes or no".
- A stronger option exists for later (decision D2): bind the data partition's unlock to a
  Sūveryn-signed measurement of the boot file (systemd's signed PCR 11 policy). Then a modified
  system can't unlock the office's data at all. It costs an extra failure mode (a boot file the
  policy doesn't cover asks for the recovery key), so it isn't proposed for the first version.

## The installer: the same system, in installer mode

The USB stick holds the **same** system and boot file as an installed appliance, plus the models:

| # | USB partition | Contents |
|---|---|---|
| 1 | EFI system partition | The same boot file. The stick has no data partition, and that is what starts installer mode (`suveryn-boot-mode` in the initrd: `/var` in memory) |
| 2 | System | Identical to an installed slot |
| 3 | System verity | Its fingerprints |
| 4 | `suveryn-bundles` (exFAT, label `SUVBUNDLES`; the rest of the stick) | Signed model bundles (#36) |

One release produces one system, used both to install and to run: nothing to keep in step between
an "installer distribution" and the product. In installer mode the console installer (#37, C1 to C7)
is the only thing that starts. It:

1. checks the hardware: GPU, memory, TPM 2.0 (C2);
2. creates the partition table on the chosen disk (systemd-repart, from definitions in the image);
3. copies system and verity to slot A **block for block** and checks the copy against the root hash;
4. creates the data partition: LUKS2 with a key made on this machine, enrolled to the TPM (PCR 7)
   and to a recovery key shown once (C7) (#34);
5. leaves the appliance's identity to the installed system's first start, which makes it on the
   data partition: UUID and Ed25519 key, sealed by the TPM where possible, never stored in plain
   form (#35; `suveryn-identity.service`, so it is sealed by the system that will use it);
6. checks and copies the chosen model bundles (#36, C3);
7. writes the name and network settings (C4), installs the boot file, and restarts into slot A.

The `first-boot.env` file on the EFI partition, as used today, goes away: nothing the office sets
ever touches the unencrypted EFI partition.

## First boot and provisioning

`suveryn-provision` stays, split cleanly:

- **Image build** (`suveryn-provision image`, as today): everything that is the same for every
  office goes into the system. That now also includes what first boot writes today outside `/var`:
  the systemd units (including the network mode's), the nginx snippets, the reader sandbox's sudo
  rule, Keycloak's built server, and Python's precompiled files (the system is read-only, so they
  can't be written later).
- **First boot** (after the web setup, #37): everything unique to the office, all of it on the data
  partition: database roles and passwords, Keycloak's realm and secret, TLS, the site, the network
  mode, the models' settings.

What changes because the system is read-only:

| Today | Then |
|---|---|
| ufw writes its rules to `/etc/ufw` | A fixed inbound nftables table in the image (80 and 443 in); the network mode's outbound table as built in #38 |
| The local CA is added to `/etc/ssl/certs` (`update-ca-certificates`) | A trust bundle on the data partition, given to the services that need it (`SSL_CERT_FILE`) |
| `/etc/hosts` gets the public name | A link to the data partition (above) |
| unattended-upgrades applies Ubuntu's security fixes | Fixes arrive as sūveryn updates (#40, decision D3) |
| `/etc/resolv.conf` written at first boot | Fixed in the image: the appliance's own resolver (#38) |

### The services in the image (#47)

What `suveryn-provision image` installed on an ordinary Ubuntu goes into the read-only system, in four
stages, each its own change (decided 23 October 2026):

1. **System and layout** (`mkosi.conf.d/30-services.conf`, the `services/` tree, the appliance only):
   the run-time packages (provision's `RUNTIME_PACKAGES`), the services' users (`sysusers.d`), their
   folders on the data partition (`tmpfiles.d`), the reader's sudo rule, PostgreSQL without a cluster
   (made at first boot, its configuration on the data partition), a fixed inbound table instead of
   ufw (80 and 443, DHCP, mDNS; both profiles), the Packer image's hardening (sysctl, no core dumps,
   a 1 GB journal; both profiles), and the trust bundle on the data partition
   (`/etc/suveryn/tls/ca-bundle.crt`, given to the gateway as `SSL_CERT_FILE`).
2. **sūveryn itself**: suveryn-core and its Python, compiled; the embedding and Docling models in
   the read-only system (not the data partition); the chat UI; Keycloak, its writable parts on the
   data partition and its host name from its environment rather than its configuration file;
   llama-server from `prebuild.sh`, with the CUDA libraries it links against; the units, enabled in
   the image and started only once first boot has written their settings.
3. **The GPU driver**: Ubuntu's own NVIDIA 580 driver with the open kernel modules
   (`linux-modules-nvidia-580-open-generic`, from the `restricted` component), built and signed by
   Canonical for linux-generic (they load with Secure Boot on, #45; cards from Turing on, which every
   supported card is), nouveau kept off, and its licence text (#49). Then the real system is measured
   against the 32 GiB slot.
4. **First boot on the read-only system**: the web setup's real provisioning, the language models
   only from the signed bundles the installer copied (one path), the network mode's outgoing units
   and resolver, Keycloak's temporary administrator removed once the office's exists, and a test
   that first boot writes nothing outside the listed paths.

Stages 1 to 3 are built, and of stage 4 the models and the network mode: the language models are
used where the installer put their bundles (`/var/lib/suveryn/bundles/model/…`, checked against
suveryn-core's manifest, never copied; media or a download only when no bundle has them), and the
outgoing rules and resolver are enabled in the image and start once first boot has set a mode, with
`/etc/resolv.conf` linking to `/run/suveryn/resolv.conf` (systemd-resolved's stub until
`suveryn-dns` runs). Keycloak's temporary administrator is removed once the office's exists and
`verify` has passed (#55). Still to do in stage 4: the test that first boot writes nothing outside the
listed paths, and first boot on real hardware (#48), where the read-only system itself refuses any
write outside the data partition. In stage 2,
everything that isn't from Ubuntu's archive is built beforehand on a Pod by `image/mkosi/prebuild-services`
into `image/mkosi/prebuilt/`, which the image build copies in and checks against the pins
(`suveryn-provision check-prebuilt`): no network, git or compiler inside the image build.
`/opt/suveryn/core` carries no git history; the realm builder reads the realm file there and checks
it against `realm-lock.json`'s sha256.

Built and inspected on a virtual machine (22 and 23 October 2026): slot A holds 14.5 GiB of its 32.
The services come to about 13 GB (12 GiB as slot A's uncompressed EROFS), and the prebuild takes about
25 minutes on 32 cores ([`image/mkosi/README.md`](../../image/mkosi/README.md)). mkosi needs a host
where it may create namespaces (a virtual or real machine, not a container like a RunPod pod); the
prebuild runs in an Ubuntu 26.04 + CUDA 13.4 container or machine, with no GPU. Those figures are from
the 24.04 image; the image is built on 26.04 since #57. The Packer build stays for developing and
testing suveryn-core on a Pod.

### Backups (#33)

Nightly onto the office's USB disk or network share, encrypted with age for the office's recovery key
(the public half from the web setup; the private half stays offline), kept for the chosen retention
with the newest always kept, and checked by a restore test. What, how and how to restore:
[`backup/README.md`](../../backup/README.md). Built into the read-only system (`suveryn-backup.timer`);
the administrator sees the last success and failure (suveryn-core). A replacement machine restores the
newest backup from its web setup (Restore from a backup) and takes the old appliance's name.

### The clock

Two-step codes need the appliance's clock within about 30 seconds. A time server keeps it where there is one
(`ntp.ubuntu.com` in connected mode, the office's own from DHCP); where there is none, the browsers of the people
signed in do: the gateway nudges the clock slowly when they agree it is off, and an administrator can set it to
their PC's time from Status. The appliance's side, as root and within its own bounds:
[`clock/README.md`](../../clock/README.md). Built into the read-only system (`suveryn-clock-request.path`).

## Updates (#40)

- An update is one signed bundle: a new system, its verity and its boot file, signed and checked
  like a model bundle (#36, decisions D7 to D9; one verification path for models and updates).
- It comes by download (connected) or on a USB stick (air-gapped). It is verified before anything is
  written.
- **systemd-sysupdate** writes it to the slot that isn't running and adds its boot file with three
  tries. The running system is never touched.
- The appliance restarts into the new slot. A health check (the core of `suveryn-provision verify`)
  marks it good. If it fails three times, systemd-boot starts the previous slot again by itself.
- An expired support agreement doesn't block an update (Filip, 28 October 2026): *Updates and models*
  says support has ended and installs anyway, and everyone who signs in sees the notice (#35, #41).
  Nothing is ever blocked by the agreement.

### The office's data during an update

**An update never writes to the data partition.** Both system slots use the same data partition;
an update only writes the system slot that isn't running. Documents, conversations, settings,
accounts and models stay where they are, and the new system opens them when it starts.

When a new version changes the database's structure (a new table or column, #32):

1. **A backup first.** The update starts only after a fresh, successful backup (#33). No backup,
   no update.
2. **Nobody works during the update.** The new system starts with sign-in closed (the gateway
   answers "being updated" and Keycloak's login isn't served) until step 4 has passed. So no one
   can change data between the backup and the end of the update.
3. **The structure changes all or nothing.** Each version's changes run once, at the new version's
   first start, in a single PostgreSQL transaction (PostgreSQL changes structure transactionally).
   If anything in it fails, the database is exactly as it was: never half converted. Changes only
   add or extend; nothing the previous version needs is removed in the same update.
4. **Then the health check.** Passed: the new version becomes the normal one and sign-in opens.
5. **Failed: back to the previous system, by itself.** If the structure change didn't complete, the
   previous system finds its database unchanged. If it completed and something else failed, the
   appliance restores the backup from step 1 before starting the previous system. Because no one
   could work in between (step 2), that backup is complete: nothing is lost.

Limits, stated plainly:

- **Going back is automatic only during the update.** Once people have worked on a new version,
  going back would mean restoring the pre-update backup and losing that work. Problems found later
  are fixed by a new update, not by going back.
- **A new PostgreSQL major version** (18 to 19, every few years) changes the database's files
  themselves (`pg_upgrade`). That is its own, planned update step, with a backup first and the
  same closed sign-in, never part of a routine update.

## Remote support (#43): connected only

Decided 14 October 2026: remote support is possible, but never on an air-gapped appliance.

- **Off by default.** The office's administrator starts a support session from the admin dashboard
  or the console, for a fixed time (proposed: up to 8 hours), tied to a support request in the
  portal (#35's single-use code).
- **The appliance connects outwards** to sūveryn's support relay. No port is opened on the office
  network, and the relay is an approved destination only while a session runs (#38).
- **The engineer signs in with a short-lived credential** issued by the portal for that request, to a
  support account that can read the system's state and logs (never document text) and run the
  diagnostics. More than that needs the administrator's explicit consent in the session.
- **Everything is visible.** Administrators see a running session, every session is logged (who,
  when, what), and it ends by itself.
- **Nothing persists.** The system is read-only, so a session can't change what runs after a
  restart; "system intact" stays true.
- **Air-gapped:** no session can be started. The administrator exports a diagnostics file (counts,
  versions, logs without content) for the partner or Sūveryn Labs.

Building it needs a relay service on sūveryn's side, next to the portal. The appliance part (the
tunnel client and the support account) isn't in the image yet. It is planned (decision D6, #43):
connected mode only, inert until a session runs.

## How it's built

**The image is built with mkosi** (systemd's image builder, in Ubuntu's archive; decision D1)
instead of installing Ubuntu inside a Packer/QEMU machine. mkosi builds the read-only system, its
verity, the boot file and the partition definitions directly from Ubuntu's packages, without running
an installer ([`image/mkosi`](../../image/mkosi/README.md)). The sūveryn services are built beforehand
by `image/mkosi/prebuild-services` into `image/mkosi/prebuilt/` (suveryn-core compiled, llama-server
with CUDA and its libraries, Keycloak, the chat UI); the image build copies them in and checks them
against the pins (`suveryn-provision check-prebuilt`). The Packer build stays only for developing and
testing suveryn-core on a Pod, until it is retired.

## Testing

- **In a virtual machine with a virtual TPM** (QEMU, OVMF, swtpm): a small test system without the
  GPU parts boots, installs from a virtual USB stick, unlocks with the TPM, refuses a changed system
  block, asks for the recovery key when the TPM state changes, updates into slot B and falls back to
  A when B is broken. This runs on any machine, slowly without hardware acceleration, and
  faster on one with KVM (decision D4).
- **On real hardware with the GPU**, once: the full image from a real USB stick to a working chat
  over HTTPS, as `image/README.md` already plans.

## Build order

| Step | Ticket | What | Tested |
|---|---|---|---|
| 1 | #39 | mkosi build: read-only system with verity, boot file, partition definitions, the `/etc` links; boots with an unencrypted data partition | VM |
| 2 | #34 | Encrypted data partition, TPM unlock, recovery key and its console prompt | VM with swtpm |
| 3 | #35 | Identity at installation; boot measurement and root hash reported | VM |
| 4 | #36 | Bundle partition and verification | VM |
| 5 | #37 | Installer mode and the console screens | VM, then a real USB stick |
| 6 | #40 | Slot B, updates from a folder or download, the health check and fall-back | VM |
| 7 | #43 | The support session (after the portal's relay exists) | VM |

## Decisions (14 October 2026)

| # | Decision | Outcome |
|---|---|---|
| D1 | Build the image with mkosi instead of Packer | **Yes**: mkosi builds the read-only system directly; Packer goes |
| D2 | Bind the data partition to sūveryn's signed boot measurement (PCR 11) | **Postponed**: PCR 7 as decided in #34; revisit before the first customer |
| D3 | Ubuntu security fixes only through sūveryn updates (no apt on the appliance) | **Yes**, with a commitment: a monthly update, and urgent fixes within days |
| D4 | A machine with KVM for the image tests | **Not available yet.** Until it is, the tests run with a small test system on a machine without KVM (slow, without hardware acceleration); the full image waits for KVM or real hardware |
| D5 | No swap on disk | **Yes** (zram if memory runs short) |
| D6 | Remote support for connected appliances only, as above (#43) | **Yes**, details in #43 |

## Decisions for signed bundles (14 October 2026, #36)

| # | Decision | Outcome |
|---|---|---|
| D7 | How bundles are signed | **Ed25519, checked with openssl** (already in the system for the identity). A signed manifest lists every file's size and SHA-256 |
| D8 | Where the private signing keys live | **AWS KMS** (key spec `ECC_NIST_EDWARDS25519`): the key never leaves it, and only a 108-byte text with the manifest's SHA-256 is sent to be signed, never the files |
| D9 | How a key is replaced | **Two trusted keys in every system**, the current one and a spare. A lost or leaked key is retired by an update, signed with the other, that leaves it out and brings a new spare |
