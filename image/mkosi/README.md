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
| `mkosi.images/initrd/` | Our initrd: mkosi's own, plus the TPM libraries and `crypttab`, so it opens the data partition |
| `mkosi.repart/` | What a release brings: the EFI partition (1 GiB), slot A (EROFS, 32 GiB) and its verity. mkosi puts the root hash in the boot file (`roothash=`) |
| `mkosi.extra/` | Files in the system: `/etc/fstab` (`/var` from the encrypted data partition in the initrd, `noexec,nosuid,nodev`; `/tmp` in memory; the machine-id), `/etc/crypttab`, the tmpfiles that create the office's folders, DHCP by default, `/usr/lib/suveryn/install-disk`, `identity` and `system-state` with `suveryn-identity.service`, `bundle` and `bundle-keys/` |
| `mkosi.postinst.chroot` | The `/etc` links into the data partition (only those the design lists), a copy of the image's own `/var` restored on a fresh data partition, no apt timers, no pcrlock |
| `target/appliance/`, `target/test/` | The whole disk for systemd-repart, shipped as `/usr/lib/suveryn/repart.d` |
| `mkosi.profiles/test/` | The test system: a small kernel, small partitions and the self-test |
| `test/` | `test_mkosi_config.py` (any machine) and `run-vm-tests` (a virtual machine with a TPM) |

## How the pieces fit

- **Verified root.** The boot file carries `roothash=`; systemd's verity generator opens slot A with
  it as `/dev/mapper/root` and mounts it read-only. A block that doesn't match is a read error.
- **`/var` in the initrd.** fstab's `x-initrd.mount` makes the initrd mount the data partition
  before the switch to the real system, so `/var` and the links into it work from the first moment.
- **The machine-id** is the one exception to "link into `/var`": systemd won't follow a link for
  `/etc/machine-id`. The image has an empty `/etc/machine-id`, and fstab bind-mounts
  `/var/lib/suveryn/host/machine-id` over it in the initrd. The installer creates that file empty on
  the data partition; systemd fills it on the first boot and reads it on every boot after.
- **The image's own `/var`** (what packages put there) would be hidden by the data partition. It is
  kept in `/usr/share/factory/var`, and systemd-tmpfiles copies whatever is missing on a fresh data
  partition.

## The encrypted data partition (#34)

- **Installing** (`/usr/lib/suveryn/install-disk <disk>`, the console installer's disk step): erases
  the disk and applies `/usr/lib/suveryn/repart.d`. The EFI partition and slot A with its verity are
  copied block for block from the stick; slot A gets the partition UUIDs derived from the root hash
  (systemd finds a verity root by them; a block copy doesn't keep them). The data partition is made
  LUKS2 with a random key that exists only in memory, then the TPM (PCR 7) and a recovery key are
  enrolled and that key is removed. The data partition then has exactly two ways in: the TPM and the
  recovery key. The recovery key (systemd's modhex format, 8 groups of 8) is printed once.
- **Booting**: the initrd's `crypttab` opens the data partition with the TPM, without a prompt. When
  the TPM can't (a firmware or Secure Boot change, a replaced TPM), the console asks for the recovery
  key ("Please enter recovery key for disk suveryn-data"): five typed attempts. systemd counts the
  failed TPM attempt as one of the tries, hence `tries=6`.
- **PCR 7 only, never pcrlock.** systemd 255 would generate a pcrlock policy at every boot and
  `systemd-cryptenroll` would then use it; its units are masked and install-disk passes
  `--tpm2-pcrlock=`. Stronger bindings are decision D2 (postponed).
- **`dmsetup` is in the image** for its udev rules: without them libdevmapper waits for udev forever,
  and repart's encryption hung.

Known, to finish with the console installer (#37):

- After five wrong keys the unlock gives up, and the system starts a new round of prompts. The
  console screen for this case is C8b ("disk won't unlock").
- On the very first boot systemd logs "Failed to write /etc/machine-id: Invalid argument", and then
  saves the machine-id anyway: it is the same on every later boot (the tests check it).

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
python3 -m pytest image/mkosi/test                     # the configuration against the design
sudo python3 -m pytest image/mkosi/test/test_identity.py   # the identity tool, host key only (no TPM)
python3 -m pytest bundles/tests                         # signed bundles, both sides
sudo image/mkosi/test/run-vm-tests                      # the test system, in a virtual machine with a TPM
```

`run-vm-tests` (QEMU with UEFI firmware and a virtual TPM, swtpm) reads the self-test's lines on the
serial console, and types at the recovery-key prompt:

1. **install**: the test image as the stick, an empty second disk; `install-disk` partitions it,
   copies slot A (verified against the root hash), encrypts the data partition with the TPM (PCR 7)
   and a recovery key only, and leaves the stick untouched;
2. **boot the installed disk twice**: the TPM opens the data partition without a prompt, and every
   rule holds: root from the verity device, read-only (EROFS), can't be written, the boot file's root
   hash, verified, every block readable; `/var` the encrypted data partition, `noexec,nosuid,nodev`,
   nothing runs from it; `/tmp` in memory; no swap; the `/etc` links; the image's `/var` restored;
   the network up; the same machine-id on both boots; the identity made, sealed to the TPM
   (`host+tpm2`), its private key nowhere in plain form, signing and verifying (also with `openssl`
   alone); the system state (release, slot, root hash, verity, PCR 11); the same identity on both boots;
   two bundle keys trusted; on the first boot, of three test bundles on a second disk the signed one
   verifies and is installed onto the data partition (without the file its manifest doesn't list),
   a changed one and one signed with an unknown key are refused; the installed bundle still verifies
   on every later boot;
3. **TPM replaced** (a new, empty TPM): the console asks for the recovery key, refuses a wrong one,
   accepts the right one; every rule holds and the machine-id is unchanged; the identity is kept but
   refuses to sign (sealed to the old TPM);
4. **wrong keys**: after five, the unlock gives up and nothing boots;
5. **a changed block in slot A**: verity refuses it ("data block … is corrupted").

Until installer mode exists, the test's stick has an encrypted data partition of its own, opened with
a test passphrase typed at the prompt. Without KVM a boot takes two to five minutes and the whole
run about 25; with KVM, a few minutes.
