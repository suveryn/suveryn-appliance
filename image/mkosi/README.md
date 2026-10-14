# The read-only system (mkosi)

The appliance's system as the design describes it ([`docs/design/appliance-architecture.md`](../../docs/design/appliance-architecture.md),
tracker #39): read-only, verified block by block, with everything the office writes on a separate
data partition. Built with [mkosi](https://github.com/systemd/mkosi) (decision D1), which replaces
the Packer build in [`image/`](../README.md) once this system carries the sūveryn services (a later
step; until then both exist).

**State (step 1 of the design's build order):** the system, its verity, the boot file and the
partition definitions; the `/etc` links and `/var` on the data partition. The data partition is not
yet encrypted (#34), the installer doesn't exist yet (#37), and the system doesn't carry the sūveryn
services yet.

## Build

```bash
pip install "git+https://github.com/systemd/mkosi.git@v25.3"   # in a venv; pinned
cd image/mkosi
sudo mkosi -f build                  # the appliance: linux-generic and firmware, slot A of 32 GiB
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
| `mkosi.conf`, `mkosi.conf.d/` | Ubuntu 24.04, the packages, the boot file. The appliance adds `linux-generic` and `linux-firmware` |
| `mkosi.repart/` | What a release brings: the EFI partition (1 GiB), slot A (EROFS, 32 GiB) and its verity. mkosi puts the root hash in the boot file (`roothash=`) |
| `mkosi.extra/` | Files in the system: `/etc/fstab` (`/var` from the data partition in the initrd, `noexec,nosuid,nodev`; `/tmp` in memory; the machine-id), the tmpfiles that create the office's folders, DHCP by default |
| `mkosi.postinst.chroot` | The `/etc` links into the data partition (only those the design lists), a copy of the image's own `/var` restored on a fresh data partition, no apt timers |
| `target/appliance/`, `target/test/` | The whole disk for systemd-repart: matches the EFI partition and slot A, adds slot B (empty) and the data partition. The installer (#37) applies `appliance/` |
| `mkosi.profiles/test/` | The test system: a small kernel, small partitions and the self-test |
| `test/` | `test_mkosi_config.py` (any machine) and `run-vm-tests` (a virtual machine) |

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

## Tests

```bash
python3 -m pytest image/mkosi/test                     # the configuration against the design
sudo image/mkosi/test/run-vm-tests                      # the test system, in a virtual machine
```

`run-vm-tests` does the installer's part on a copy of the test image (systemd-repart with
`target/test`), boots it twice in QEMU with UEFI firmware (OVMF), and reads the self-test's lines on
the serial console. It checks that:

- the root comes from the verity device, read-only (EROFS), can't be written, with the boot file's
  root hash, and verity reports it verified;
- every block of the root can be read;
- `/var` is the data partition, `noexec,nosuid,nodev`, and a program copied there can't run;
- `/tmp` is in memory and there is no swap;
- the `/etc` links work, the image's `/var` is restored, and the network comes up by DHCP;
- the machine-id survives a restart;
- after one byte of slot A is changed, verity refuses the block ("data block … is corrupted").

Without KVM a boot takes about two minutes and the whole run about eight; with KVM, seconds.
