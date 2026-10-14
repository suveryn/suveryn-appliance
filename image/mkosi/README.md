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
sudo mkosi -f build                  # the appliance: linux-generic and firmware, slot A of 32 GiB
test/make-test-keys                  # once: the test system's bundle keys (#36), never committed
sudo mkosi -f --profile test build   # the test system: the same layout, small (for run-vm-tests)
```

Needs Ubuntu 24.04 as the build host (systemd 255: `systemd-repart`, `ukify`), and `bubblewrap`,
`erofs-utils`, `dosfstools`, `mtools`, `kmod`, `cpio`, `systemd-boot`, `systemd-ukify`. The result
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
| `mkosi.extra/` | Files in the system: `/etc/fstab` (`/tmp` in memory), an empty `/etc/crypttab`, the tmpfiles that create the office's folders, DHCP by default, `/usr/lib/suveryn/install` and `install-disk`, `identity` and `system-state` with `suveryn-identity.service`, `bundle` and `bundle-keys/` |
| `mkosi.postinst.chroot` | The `/etc` links into the data partition (only those the design lists), a copy of the image's own `/var` restored on a fresh data partition, no apt timers, no pcrlock |
| `target/appliance/`, `target/test/` | The whole disk for systemd-repart, shipped as `/usr/lib/suveryn/repart.d` |
| `make-stick` | Makes an installer stick: the disk image and a bundles partition with signed bundles |
| `mkosi.profiles/test/` | The test system: a small kernel, small partitions and the self-test |
| `test/` | `test_mkosi_config.py`, `test_install.py` (any machine), `test_identity.py` (root) and `run-vm-tests` (a virtual machine with a TPM) |

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
- **Booting**: `suveryn-boot-mode` opens the boot disk's data partition with the TPM, without a prompt. When
  the TPM can't (a firmware or Secure Boot change, a replaced TPM), the console asks for the recovery
  key ("Please enter recovery key for disk suveryn-data"): five typed attempts. systemd counts the
  failed TPM attempt as one of the tries, hence `tries=6`.
- **PCR 7 only, never pcrlock.** systemd 255 would generate a pcrlock policy at every boot and
  `systemd-cryptenroll` would then use it; its units are masked and install-disk passes
  `--tpm2-pcrlock=`. Stronger bindings are decision D2 (postponed).
- **`dmsetup` is in the image** for its udev rules: without them libdevmapper waits for udev forever,
  and repart's encryption hung.

Known, to finish with the console installer (#37):

- After five wrong keys the unlock gives up and the initrd stops in emergency mode (nothing else
  starts; the root account is locked, so only a restart helps). The console screen for this case is
  C8b ("disk won't unlock"), with the prompt itself.
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

Still to come in #37: the console screens themselves (C1 to C7), the console after the first boot
(C8) and the unlock screen (C8b), mDNS (`<name>.local`), the keyboard layout.

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
reads the self-test's lines on the serial console, and types at the recovery-key prompt:

1. **install from the stick**: the stick (the test image and three test bundles: a signed one, a
   changed one, one signed with an unknown key) and an empty second disk. The stick starts in
   installer mode, without a prompt: slot A from the stick, `/var` in memory, no data partition
   opened, no identity. The installer lists the bundles (the signed one valid, the other two refused),
   refuses a bad name and the changed bundle without writing anything, then installs: slot A copied
   (verified against the root hash), the data partition encrypted with the TPM (PCR 7) and a recovery
   key only, the signed model installed, a boot entry; the stick untouched;
2. **boot the installed disk**: the TPM opens the data partition without a prompt, and every rule
   holds: slot A from the boot disk, installed mode with its own disk's data partition; root from the
   verity device, read-only (EROFS), can't be written, the boot file's root hash, verified, every block
   readable; `/var` the encrypted data partition, `noexec,nosuid,nodev`, nothing runs from it; `/tmp`
   in memory; no swap; the `/etc` links; the image's `/var` restored; the installer's name, time zone
   and network file; the network up; the identity made, sealed to the TPM (`host+tpm2`), its private
   key nowhere in plain form, signing and verifying (also with `openssl` alone); the system state
   (release, slot, root hash, verity, PCR 11); two bundle keys trusted; the installed model verifies;
3. **boot it again with the stick attached**: the same, from its own disk; the same machine-id and
   identity;
4. **the stick beside the installed disk**: installer mode, slot A from the stick, and the installed
   disk's data partition is never opened (no prompt);
5. **TPM replaced** (a new, empty TPM): the console asks for the recovery key, refuses a wrong one,
   accepts the right one; every rule holds and the machine-id is unchanged; the identity is kept but
   refuses to sign (sealed to the old TPM);
6. **wrong keys**: after five, the unlock gives up and nothing starts;
7. **a changed block in slot A**: verity refuses it ("data block … is corrupt").

Without KVM a boot takes two to five minutes and the whole run about 40; with KVM, a few minutes.
