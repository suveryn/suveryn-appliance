# The read-only system (mkosi)

The appliance's system as the design describes it ([`docs/design/appliance-architecture.md`](../../docs/design/appliance-architecture.md),
tracker #39): read-only, verified block by block, with everything the office writes on a separate
data partition. Built with [mkosi](https://github.com/systemd/mkosi) (decision D1), which replaces
the Packer build in [`image/`](../README.md) once this system carries the sūveryn services (a later
step; until then both exist).

**State (steps 1 and 2 of the design's build order):** the system, its verity, the boot file and
the disk's partition definitions; the `/etc` links and `/var` on the data partition (#39). The data
partition is encrypted (LUKS2), unlocked by the TPM (PCR 7) or a recovery key, and the installer's
disk step (`install-disk`) creates all of it (#34). Not yet: the console installer and installer
mode (#37), and the sūveryn services in this system.

## Build

```bash
pip install "git+https://github.com/systemd/mkosi.git@v25.3"   # in a venv; pinned
cd image/mkosi
sudo ./prebuild-services             # the appliance's sūveryn services, built on this host into prebuilt/ (#47)
sudo mkosi -f build                  # the appliance: linux-generic and firmware, the services, slot A of 32 GiB
test/make-test-keys                  # once: the test system's bundle and portal keys (#36, #37), never committed
test/make-test-ui                    # the chat UI's build for the test system's web setup (#37), never committed
sudo mkosi -f --profile test build   # the test system: the same layout, small (for run-vm-tests)
```

The appliance's build stops without `prebuilt/`, or when a part in it isn't the pinned one
(`suveryn-provision check-prebuilt`). `prebuild-services` needs an Ubuntu 24.04 host with CUDA 12.8 (no
GPU needed; it installs Java and the build tools itself) and takes about 25 minutes on 32 cores; the
services come to about 13 GB (12 GiB as slot A's uncompressed EROFS). mkosi itself needs a host where
it may create namespaces: a virtual or real machine, not a container like a RunPod pod (Pod check of
#47: "mkosi was forbidden to unshare namespaces"). Both together need about 60 GB free.

Needs Ubuntu 24.04 as the build host (systemd 255: `systemd-repart`, `ukify`), and `bubblewrap`,
`erofs-utils`, `dosfstools`, `mtools`, `kmod`, `cpio`, `systemd-boot`, `systemd-ukify` (and `systemd-repart`
where it is a package of its own, as on Ubuntu 26.04). The result
is in `mkosi.output/`: `suveryn_<version>.raw` (the disk image: EFI partition, slot A, its verity),
and the boot file, kernel and initrd beside it.

Why mkosi 25.3 and not Ubuntu's mkosi 20.2: 20.2's built-in initrd asks for packages under their
pre-24.04 names and can't build for noble. Packages come from Ubuntu's archives **over HTTPS only**
(`mkosi.sandbox/etc/apt/sources.list.d/mkosi.sources`; mkosi would otherwise use HTTP).

## What is where

| Path | What |
|---|---|
| `mkosi.conf`, `mkosi.conf.d/` | Ubuntu 24.04, the packages, the boot file. The appliance adds `linux-generic` and `linux-firmware`, and an initrd that holds only what reaches the disk |
| `mkosi.images/initrd/` | Our initrd: mkosi's own, plus the TPM libraries and `suveryn-boot-mode` (installed appliance or installer, and `/var` for it) with `boot-disk` and its udev rule |
| `mkosi.repart/` | What a release brings: the EFI partition (1 GiB), slot A (EROFS, 32 GiB) and its verity. mkosi puts the root hash in the boot file (`roothash=`) |
| `mkosi.extra/` | Files in the system: `/etc/fstab` (`/tmp` in memory), an empty `/etc/crypttab`, the tmpfiles that create the office's folders, DHCP by default, `/usr/lib/suveryn/install`, `installer-ui` and `install-disk`, `identity` and `system-state` with `suveryn-identity.service`, `bundle` and `bundle-keys/` |
| `mkosi.conf.d/30-services.conf`, `services/`, `prebuild-services` | The sūveryn services (#47, the appliance only): their packages, users, folders on the data partition and sudo rule; sūveryn itself from `prebuilt/`; the units, enabled and waiting for first boot's settings |
| `mkosi.postinst.chroot` | The `/etc` links into the data partition (only those the design lists), a copy of the image's own `/var` restored on a fresh data partition, no apt timers, no pcrlock; the services' users and checks |
| `target/appliance/`, `target/test/` | The whole disk for systemd-repart, shipped as `/usr/lib/suveryn/repart.d` |
| `make-stick` | Makes an installer stick: the disk image and a bundles partition with signed bundles |
| `mkosi.profiles/test/` | The test system: a small kernel, small partitions and the self-test |
| `test/` | `test_mkosi_config.py`, `test_install.py`, `test_installer_ui.py` (any machine; the console installer in a pseudo-terminal), `test_identity.py` (root) and `run-vm-tests` (a virtual machine with a TPM) |

## How the pieces fit

- **Verified root.** The boot file carries `roothash=`; systemd's verity generator opens slot A with
  it as `/dev/mapper/root` and mounts it read-only. A block that doesn't match is a read error.
- **`/var` in the initrd.** `suveryn-boot-mode` mounts it before the switch to the real system, so
  `/var` and the links into it work from the first moment: the data partition on an installed
  appliance, memory on the installer stick (below).
- **The machine-id** is the one exception to "link into `/var`": systemd won't follow a link for
  `/etc/machine-id`. The image has an empty `/etc/machine-id`, and `suveryn-boot-mode` bind-mounts
  `/var/lib/suveryn/host/machine-id` over it in the initrd. The installer creates that file empty on
  the data partition; systemd fills it on the first boot and reads it on every boot after.
- **The image's own `/var`** (what packages put there) would be hidden by the data partition. It is
  kept in `/usr/share/factory/var`, and systemd-tmpfiles copies whatever is missing on a fresh data
  partition.

## The encrypted data partition (#34)

- **Installing** (`/usr/lib/suveryn/install-disk <disk>`, the installer's disk step, below): erases
  the disk and applies `/usr/lib/suveryn/repart.d`. The EFI partition and slot A with its verity are
  copied block for block from the stick; slot A gets the partition UUIDs derived from the root hash
  (systemd finds a verity root by them; a block copy doesn't keep them). The data partition is made
  LUKS2 with a random key that exists only in memory, then the TPM (PCR 7) and a recovery key are
  enrolled and that key is removed. The data partition then has exactly two ways in: the TPM and the
  recovery key. The recovery key (systemd's modhex format, 8 groups of 8) is printed once.
- **Booting**: `suveryn-boot-mode` opens the boot disk's data partition with the TPM, without a
  prompt. When the TPM can't (a firmware or Secure Boot change, a replaced TPM), the unlock screen
  (C8b, below) asks for the recovery key.
- **PCR 7 only, never pcrlock.** systemd 255 would generate a pcrlock policy at every boot and
  `systemd-cryptenroll` would then use it; its units are masked and install-disk passes
  `--tpm2-pcrlock=`. Stronger bindings are decision D2 (postponed).
- **`dmsetup` is in the image** for its udev rules: without them libdevmapper waits for udev forever,
  and repart's encryption hung.

Known:

- On the very first boot systemd logs "Failed to write /etc/machine-id: Invalid argument", and then
  saves the machine-id anyway: it is the same on every later boot (the tests check it).

## Installer mode and the installer (#37)

The installer stick holds the **same** system as an installed appliance (design note, "The installer"),
made by `make-stick`: the image's EFI partition, slot A and verity, block for block, and a fourth
partition named `suveryn-bundles` that fills the rest of the stick, exFAT (label `SUVBUNDLES`: exFAT
labels hold 11 characters), with the signed bundles in folders of their own.

```bash
sudo image/mkosi/make-stick mkosi.output/suveryn_<version>.raw /dev/sdX --bundles ./qwen3-30b-a3b-2026.10.1 …
```

- **One system, two modes, decided in the initrd** (`suveryn-boot-mode`, before anything else
  starts). The disk the firmware started from (systemd-boot's `LoaderDevicePartUUID`) has a partition
  named `suveryn-data`: **installed**, it is unlocked and mounted on `/var`. It has none:
  **installer**, `/var` is in memory and nothing on any disk is opened. The mode is in
  `/run/suveryn/boot-mode`; `/run/suveryn/installer-mode` exists for units' conditions (the identity
  isn't made on the stick).
- **Only the boot disk counts.** A stick started on a machine that has an appliance never opens that
  appliance's data, and an appliance never opens a data partition on another disk. Slot A has the
  same partition UUIDs on the stick and on an appliance of the same release (they come from the root
  hash), so a udev rule gives the boot disk's partitions priority in `/dev/disk/by-partuuid`, and
  slot A is opened only once the boot disk is there (`boot-disk wait`, before
  `systemd-veritysetup@root`). The tests check that slot A comes from the boot disk in both cases.
- **`/usr/lib/suveryn/install`** does everything the console installer will (C2 to C7), in installer
  mode only: checks the answers (name, time zone, network) and every chosen bundle in full, before the
  disk is touched; partitions it, copies the system and encrypts the data partition (install-disk);
  writes the name, the time zone (`/etc/localtime` links to the data partition), the network
  (`/etc/systemd/network/10-suveryn.network`, matched by MAC address: DHCP or a fixed address) and the
  models (each checked again while it is copied) onto the data partition, with a record of the
  installation (`/var/lib/suveryn/installed.json`); then enrols the TPM and the recovery key and adds a
  firmware boot entry. Progress is `step N/4: …` on stderr; `--json` prints the result, with the
  recovery key (shown once, C7). `install --list-models [--json]` checks the stick's bundles (C3).

### The console installer (C1 to C7)

`/usr/lib/suveryn/installer-ui` (`suveryn-installer.service`, on the first console, in installer mode
only) shows the approved screens and hands the answers to `install`:

| Screen | What it asks or shows |
|---|---|
| C1 | Language: English, Nederlands or Français; the screens switch at once, and it is stored for the web setup. Keyboard layout (loaded at once; a test field), time zone |
| C2 | The hardware check (NVIDIA card and its memory, cores, TPM 2.0, memory against the recommended 64 GB) and the target disk; the stick and disks too small for the partition definitions can't be chosen. No TPM, no NVIDIA card or no usable disk stops here |
| C3 | The stick's models, each checked in full in the background from the start: signed ones offered (title, description, size, default from the bundle's manifest), others skipped with the reason; whether they fit on the disk |
| C4 | The Ethernet port, the name (`<name>.local`), DHCP (the lease shown) or a fixed address; after 30 s without a lease it switches to a fixed address, with Retry DHCP (C4b) |
| C5 | The summary; type ERASE to install |
| C6 | Progress, from `install`'s steps; keys are disabled. A failure says which step and why, and goes back to the summary |
| C7 | The recovery key, once; typing its first group enables Restart |

Keys: arrows select, Tab moves, Space ticks, Enter continues (opens a list on a list field), Esc goes
back, F1 shows help, Ctrl+L redraws. The console looks like the design: the brand's dark theme as the
console's 16 colours (`console-colours`, loaded with `setvtrgb`) and Terminus with the character set
console-setup calls "Vietnamese" (`/etc/default/console-setup`): of its Terminus fonts it is the one
with the "ū", the French and Dutch letters and the half blocks the QR code needs. No console font has
a "✓", so the screens show "√" there.

The keyboard layout is kept on the data partition (`/etc/default/keyboard` links there, design note) and
loaded at every start by console-setup. Its service runs `setupcon` directly (a drop-in): the
package's own script tries to save into `/etc/console-setup`, which is read-only here, so it failed
and the chosen layout never reached the console.

**Languages.** Every word on these screens is in `installer-text.json`, in English (the approved
design's copy), Dutch (formal: u) and French (vous); a test checks that every text exists in all three
with the same values filled in, and the house style (no em dashes, no informal forms, sūveryn in lower
case mid-sentence). Sizes follow the language (17,0 GB; 17,0 Go), and the confirmation word too
(ERASE, WISSEN, EFFACER). A model's description can be given in each language
(`description_nl`, `description_fr` in its manifest); without one, the English shows. The installer
engine's own messages (a failed step's detail) stay in English. The Dutch and French copy is a
first version: to be read by a native speaker before release.

### The unlock screen (C8b)

When the TPM can't open the data partition, `unlock-screen` (in the initrd) shows the design's
"Disk won't unlock" screen on the console, in the brand's colours and font, and asks for the recovery
key: five attempts; dashes, spaces and capitals optional (the key uses only letters that sit in the
same place on AZERTY and QWERTY). The office's language isn't known yet (it is on the locked disk), so
the screen is English with its essential lines in Dutch and French. Once the key opens the disk, the
TPM is enrolled again (PCR 7, as at installation; `systemd-cryptenroll`, in the initrd), so the next
start is unattended. The old TPM's slot is removed first, in a step of its own: asked to enrol and wipe
at once, `systemd-cryptenroll` does nothing when a slot for the same PCRs exists, even one the new TPM
can't open. After five wrong keys it says what that means and waits for Ctrl+Alt+Del; nothing
else starts. The key passes only through a file in the initrd's memory, removed once tried. The
initrd carries `kbd` and the Terminus font for this (about 3 MB more).

### The console after installation (C8)

`/usr/lib/suveryn/console-ui` (`suveryn-console.service`, on the first console of an installed appliance,
never on the stick) shows how to reach the appliance: `https://<name>.local` and its IP address, the
web setup's certificate fingerprint while setup is open, the network, how the disk was unlocked (by the
TPM, or with the recovery key and linked to the TPM again), setup, the appliance ID, support (no valid
manifest yet: the days until the banner shows for everyone) and the time, in the language chosen at
installation. The menu:

1. **network details**: the name on the network, each port's MAC address, addresses, gateway, DNS;
2. **change network settings**: the installer's network screen (C4); the new settings go to the data
   partition and are applied at once (`networkctl reload`), the name too;
3. **the registration QR code** (`qrencode`, in half blocks), with the appliance's ID and key
   fingerprint. The portal's address in it is a placeholder (`PORTAL_REGISTER`) until the portal exists;
4. **restart** and 5. **shut down**, each confirmed first.

The certificate fingerprint and "setup complete" come from files the web setup will write
(`/var/lib/suveryn/etc/tls/setup-cert.pem`, `/var/lib/suveryn/etc/setup-complete`); until the web setup
exists, the console shows the waiting state without a fingerprint.

### `<name>.local`

systemd-resolved answers for the appliance's name over mDNS on the office's network
(`resolved.conf.d/50-suveryn-mdns.conf`, `MulticastDNS=yes` in each port's network file, the installer's
too). The network mode's outgoing rules let mDNS out on the office's link (224.0.0.251 and ff02::fb,
port 5353). The inbound rules, when they come to the image, must let UDP 5353 in.

The web setup (screens 0 to 8, in the browser) is suveryn-core's `setup.html` with
[`setup`](../../setup/README.md) on the appliance's side, in this image (`mkosi.conf.d/20-web-setup.conf`):
nginx, the setup service and its units, at `/opt/suveryn/appliance`. It opens at the first start of the
installed appliance, writes `setup-cert.pem` while open and `setup-complete` when it closes. Its
provisioning needs the sūveryn services, which come into this image with #39; until then the test
system runs it with a stand-in (`run-vm-tests` step 4b). The chat UI's build isn't in the appliance
image yet either (#39); the test system carries one (`test/make-test-ui`).

## The appliance's identity and system state (#35)

- **The identity** is a UUID (v4) and an Ed25519 key, made on the first boot of the installed
  system by `suveryn-identity.service` (`/usr/lib/suveryn/identity create`), only when there is none
  yet. It lives on the encrypted data partition, in `/var/lib/suveryn/identity`: `uuid`,
  `public.pem`, `identity.json` (UUID, public key, fingerprint, how it is sealed) and `private.cred`.
- **The private key is never on disk in plain form.** `openssl genpkey` writes it straight into
  `systemd-creds encrypt`, which seals it with the TPM and the host key on the data partition
  (`host+tpm2`; `host` alone on a machine without a TPM). Signing decrypts it into memory and hands it
  to `openssl` through a pipe.
- **Bound to the TPM, not to PCR 7.** A firmware or Secure Boot change doesn't affect it. A replaced
  TPM (another motherboard) does: the data partition then opens with the recovery key, the identity
  stays, but it can't sign, and `identity show` says so ("Usable: NO"). Such an appliance has to be
  registered again (portal, later).
- **The fingerprint** is the SHA-256 of the raw 32-byte public key, in hex.
- `identity show [--json]`, `identity sign FILE` (base64 on stdout; `-` reads stdin), `identity
  verify FILE SIG [--public-key PEM]`. A signature is plain Ed25519: `openssl pkeyutl -verify -rawin`
  checks it with `public.pem` alone, as the portal will.
- **System state** (`/usr/lib/suveryn/system-state [--json]`) reports, without judging: the release
  (`IMAGE_ID`, `IMAGE_VERSION`), the slot it booted, the root hash from the boot file, whether verity
  checks the running system against it, the TPM's PCR 11 (the boot file's measurement) and the
  identity. The portal compares the root hash with the one Sūveryn Labs published for that release
  ("system intact"). Exit code 0 when verity is active and matches the root hash.

Still to come: the check-in that signs and sends this (with the portal), and the manifest check
(`valid_until`) once the manifest format is agreed (#36).

## Signed bundles (#36)

`/usr/lib/suveryn/bundle` checks and installs signed bundles (models now, system updates in #40)
against the public keys in `/usr/lib/suveryn/bundle-keys`: two per release, the current AWS KMS
key and a spare. Format, signing and key handling: [`bundles/`](../../bundles/README.md).

- **The appliance image** trusts `release-current.pem` and `release-spare.pem` once they are added
  ([`bundle-keys/README.md`](mkosi.extra/usr/lib/suveryn/bundle-keys/README.md)). Until then it
  trusts no key and refuses every bundle; `test_the_appliance_trusts_two_release_keys` is marked
  as expected to fail as a reminder.
- **The test image** trusts two test keys made by `test/make-test-keys` in `test/keys/` (ignored
  by git). They never reach the appliance image (a test checks it).

## Tests

```bash
python3 -m pytest image/mkosi/test                     # the configuration against the design; the installer's checks
sudo python3 -m pytest image/mkosi/test/test_identity.py   # the identity tool, host key only (no TPM)
python3 -m pytest bundles/tests                         # signed bundles, both sides
sudo image/mkosi/test/run-vm-tests                      # the test system, in a virtual machine with a TPM
```

`run-vm-tests` (QEMU with UEFI firmware and a virtual TPM, swtpm) makes a stick with `make-stick`,
reads the self-test's lines on the serial console, types at the recovery-key prompt, and drives the
console installer through a terminal emulator (pyte) the way a person would:

1. **the installer's checks**: the stick (the test image and three test bundles: a signed one, a
   changed one, one signed with an unknown key) and an empty second disk. The stick starts in
   installer mode, without a prompt: slot A from the stick, `/var` in memory, no data partition
   opened, no identity. `install --list-models` finds the signed bundle valid and refuses the other
   two; a bad name and the changed bundle are refused without writing anything; the stick untouched;
2. **install with the console installer**: C1 to C7 on the console, in Dutch: Nederlands chosen on C1
   (the screens switch at once), the empty disk chosen, the signed model offered and ticked (with its
   Dutch description) and the other two skipped with their reasons, the name typed and the DHCP lease
   shown, WISSEN typed, the progress, the recovery key read off the screen and its first group typed,
   Restart;
3. **boot the installed disk**: the TPM opens the data partition without a prompt, and every rule
   holds: slot A from the boot disk, installed mode with its own disk's data partition; root from the
   verity device, read-only (EROFS), can't be written, the boot file's root hash, verified, every block
   readable; `/var` the encrypted data partition, `noexec,nosuid,nodev`, nothing runs from it; `/tmp`
   in memory; no swap; the `/etc` links; the image's `/var` restored; the installer's name, time zone,
   keyboard layout and network file (and, on the first start, its firmware boot entry); the network
   up; the identity made, sealed to the TPM (`host+tpm2`), its private key nowhere in plain form,
   signing and verifying (also with `openssl` alone); the system state (release, slot, root hash,
   verity, PCR 11); two bundle keys trusted; the installed model verifies; the web setup open, its
   service running as `suveryn-setup` without capabilities, the setup page at `/` and its certificate;
4. **boot it again with the stick attached**: the same, from its own disk; the same machine-id and
   identity;
4b. **the web setup in a browser** (#37 part 6a): port 443 forwarded to the host, where Chromium drives
   the screens from Welcome to Complete in the setup service's real sandbox: they open in Dutch (the
   installer's language), the address in use, a refused key, a manifest for another appliance and
   the right one (signed with the portal's test key for this appliance's real ID), a share outside the
   office's network and one on it, the recovery key, provisioning (the stand-in, failing once) to
   Complete, no CSP violations. Inside, the self-test then checks setup closed for good: the units
   stopped, `/` the chat and `/setup/` 404, the answers and secrets gone, the choices kept. Every later
   start checks it stays closed;
5. **the console (C8)**: on the serial console, in Dutch: the addresses, disk, ID and support; the
   network details; the registration QR code; the network changed to a fixed address (applied at
   once, and used at every later start); shut down;
6. **the stick beside the installed disk**: installer mode, slot A from the stick, and the installed
   disk's data partition is never opened (no prompt);
7. **TPM replaced** (a new, empty TPM): the unlock screen (C8b) asks for the recovery key, refuses a
   wrong one, accepts the right one typed without dashes and in capitals, and enrolls the new TPM;
   every rule holds and the machine-id is unchanged; the identity is kept but refuses to sign
   (sealed to the old TPM);
8. **the next start with that TPM**: unattended again, no prompt;
9. **wrong keys**: after five, the unlock screen says so and waits; nothing starts;
10. **a changed block in slot A**: verity refuses it ("data block … is corrupt").

Without KVM a boot takes two to five minutes and the whole run about an hour; with KVM, a few minutes.
