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
   the system onto the disk, creates the encrypted data partition, the identity and the recovery
   key, and copies the signed models from the stick.
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
| `/etc/machine-id` | kept by systemd on `/var` | Unique per appliance |
| `/etc/systemd/network/` office part | `/var/lib/suveryn/network` | Fixed address or DHCP (#37, C4) |

Everything else stays where the image puts it. Today's provisioning writes in a few more places;
those move into the image itself (below). A test checks that nothing outside this list is written
at first boot.

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
| 1 | EFI system partition | The boot file, set to start in installer mode |
| 2 | System | Identical to an installed slot |
| 3 | System verity | Its fingerprints |
| 4 | `SUVERYN-BUNDLES` (exFAT, the rest of the stick) | Signed model bundles (#36) |

One release produces one system, used both to install and to run: nothing to keep in step between
an "installer distribution" and the product. In installer mode the console installer (#37, C1 to C7)
is the only thing that starts. It:

1. checks the hardware: GPU, memory, TPM 2.0 (C2);
2. creates the partition table on the chosen disk (systemd-repart, from definitions in the image);
3. copies system and verity to slot A **block for block** and checks the copy against the root hash;
4. creates the data partition: LUKS2 with a key made on this machine, enrolled to the TPM (PCR 7)
   and to a recovery key shown once (C7) (#34);
5. creates the appliance's identity on the data partition: UUID and Ed25519 key, sealed by the TPM
   where possible (#35);
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

## Updates (#40)

- An update is one signed bundle: a new system, its verity and its boot file (#36 and #40 decide
  the signing; one verification path for models and updates).
- It comes by download (connected) or on a USB stick (air-gapped). It is verified before anything is
  written.
- **systemd-sysupdate** writes it to the slot that isn't running and adds its boot file with three
  tries. The running system is never touched.
- The appliance restarts into the new slot. A health check (the core of `suveryn-provision verify`)
  marks it good. If it fails three times, systemd-boot starts the previous slot again by itself.
- Installers refuse updates released after the manifest's `valid_until` (#35).

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
- **A new PostgreSQL major version** (16 to 17, every few years) changes the database's files
  themselves (`pg_upgrade`). That is its own, planned update step, with a backup first and the
  same closed sign-in, never part of a routine update.

## Remote support (#43): connected only

Decided 14 October 2026: remote support is possible, but never on an air-gapped appliance.

- **Off by default.** The office's administrator starts a support session from the admin dashboard
  or the console, for a fixed time (proposed: up to 8 hours), tied to a support request in the
  portal (#35's single-use code).
- **The appliance connects outwards** to Sūveryn's support relay. No port is opened on the office
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

Building it needs a relay service on Sūveryn's side, next to the portal. The appliance part (the
tunnel client and the support account) is in the image from the start, inert until a session runs.
Details are #43's.

## How it's built

**Proposal: build the image with mkosi** (systemd's image builder, in Ubuntu 24.04) instead of
installing Ubuntu inside a Packer/QEMU machine. mkosi builds the read-only system, its verity, the
boot file and the partition definitions directly from Ubuntu's packages, reproducibly, without
running an installer. The current provisioning steps run inside it unchanged (`suveryn-provision
image`), and the heavy parts that `prebuild.sh` builds beforehand (llama-server with CUDA, Keycloak,
the chat UI) are copied in as they are now. Packer can then go (decision D1).

## Testing

- **In a virtual machine with a virtual TPM** (QEMU, OVMF, swtpm): a small test system without the
  GPU parts boots, installs from a virtual USB stick, unlocks with the TPM, refuses a changed system
  block, asks for the recovery key when the TPM state changes, updates into slot B and falls back to
  A when B is broken. This runs in Claude's workspace (slowly, without hardware acceleration) and
  faster on any machine with KVM (decision D4).
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
| D2 | Bind the data partition to Sūveryn's signed boot measurement (PCR 11) | **Postponed**: PCR 7 as decided in #34; revisit before the first customer |
| D3 | Ubuntu security fixes only through sūveryn updates (no apt on the appliance) | **Yes**, with a commitment: a monthly update, and urgent fixes within days |
| D4 | A machine with KVM for the image tests | **Not available yet.** Until it is, the tests run in Claude's workspace with a small test system (slow, without hardware acceleration); the full image waits for KVM or real hardware |
| D5 | No swap on disk | **Yes** (zram if memory runs short) |
| D6 | Remote support for connected appliances only, as above (#43) | **Yes**, details in #43 |
