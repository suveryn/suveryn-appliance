# The appliance image

> **For testing with fictional documents only. Never an office's records.** This Packer image has no
> encrypted data partition: whoever has the disk can read the documents, conversations and accounts on
> it. The appliance offices get is the read-only, verified system of the approved design
> ([`docs/design/appliance-architecture.md`](../docs/design/appliance-architecture.md)), built with mkosi in
> [`mkosi/`](mkosi/README.md), with its data partition encrypted (LUKS2, TPM or recovery key). That system
> carries the sūveryn services (#47); this build is retired once it has been installed on a real PC and
> through first boot (#48).

A **bare-metal disk image**: flashed onto the office's own server, which then runs sūveryn directly
on its hardware (like pfSense or TrueNAS). It is not a VM image. GPU passthrough into a VM is
unreliable across arbitrary office hardware, and NVIDIA's GeForce driver licence is written for
hardware the office owns, not for virtualised datacenter-style deployment.

Packer and QEMU are only the build tools: Ubuntu 24.04 is installed into a disk file inside a QEMU
machine, provisioned and hardened there, and that disk file is the image.

```bash
cd image
packer init .
PKR_VAR_build_password=$(openssl rand -base64 24) packer build .              # with KVM
sudo ./prebuild.sh /var/tmp/suveryn-prebuilt.tar.gz   # without KVM: build the heavy parts on this host first
PKR_VAR_build_password=$(openssl rand -base64 24) packer build -var accelerator=tcg -var prebuilt=/var/tmp/suveryn-prebuilt.tar.gz .
```

The result is `/var/tmp/suveryn-image/suveryn-appliance.img.zst` (raw, UEFI, GPT) and `SHA256SUMS` beside it (`-var output_dir=…` to change; never inside this repository, which is uploaded into the build).

Needs: Linux with QEMU (`qemu-system-x86`, `qemu-utils`), OVMF, `zstd`, Packer 1.16.1, network access,
and about 100 GB free. With KVM the build takes about an hour. Without it (`accelerator=tcg`, e.g. a RunPod pod) everything runs in software
emulation: the CUDA compiler takes many hours there, and Java's compiler (Keycloak's build) crashes. `prebuild.sh` builds
llama-server, Keycloak and the chat UI on the host instead (Ubuntu 24.04 with CUDA 12.8, the same pins); the image keeps
each part only when it matches what it should be built from.

## What happens when

| | Build time (`packer build`, `suveryn-provision image`) | First boot (`suveryn-first-boot.service`, `suveryn-provision first-boot`) |
|---|---|---|
| What | Everything that is the same for every office | Everything unique to this box and office |
| Network | Needed (packages, pinned downloads) | Per network mode (below) |
| OS | Ubuntu 24.04 minimal, `linux-generic`, firmware, UEFI boot from the removable path | The root file system grows to the disk |
| NVIDIA | Ubuntu's open kernel modules (branch 580, signed: Secure Boot works), CUDA runtime | **GPU licence check** on the real card (`SITE`) |
| Services | PostgreSQL 16 + pgvector; suveryn-core with its embedding and Docling models; llama-server (llama.cpp, CUDA for Ampere, Ada and Hopper); Keycloak with the theme; the reader sandbox; the chat UI; nginx with the header snippets | Database roles and passwords; the LLM files; Keycloak's host name and the production realm with a fresh client secret, built from the image's suveryn-core checkout (no network needed); TLS; the nginx site; the services started and `verify` run |
| Settings | None | Network mode, firewall and updates per mode (below) |
| Secrets | **None in the image**: no passwords, keys, certificates or machine-id | All generated on the box |

## First boot

**The web setup** (suveryn-tracker#37): when no `first-boot.env` is on the EFI partition, the first
start opens `https://<name>.local/` and someone on the office's network answers the setup screens;
[`setup`](../setup/README.md) then runs `suveryn-provision first-boot` with those answers. Nothing the
office sets touches the EFI partition. `first-boot.env` below is the earlier way. It stays in this
test image until the web setup's real provisioning (#37 part 6b) has passed end to end. What has been
tested so far is in [`setup/README.md`](../setup/README.md#testing): the screens in a browser with a
stand-in for provisioning, on any machine and on the read-only system in a VM (part 6a). The read-only
system has no `first-boot.env`.

The installer flashes the image, then writes the office's settings to the **EFI partition**, which
is FAT32 and so writable from any computer, at `suveryn/first-boot.env`:

```bash
PUBLIC_URL=https://suveryn.office.example
NETWORK_MODE=air-gapped          # connected | air-gapped
OUTBOUND=approved                # connected only: approved (the default) | all
SITE=on-premise                  # required with a GeForce card: on-premise | datacenter (refused) | test
CASES_ACCESS=members             # who sees a new case by default: members (the default) | office (everyone signed in)
CASES_KEEP=manual                # how long a closed case is kept: 6m | 1y | 5y | manual (the default: until deleted by hand)
CONVERSATIONS_KEEP=manual        # how long saved conversations are kept, from their last change: the same choices and default
MODELS_FROM=/media/suveryn-models
TLS_CERT=/boot/efi/suveryn/server.pem   # optional: the office's certificate (full chain) and key;
TLS_KEY=/boot/efi/suveryn/server.key    # without them a local CA on the box issues one
```

At the first start `suveryn-first-boot.service` copies the settings and any certificate files off
the FAT partition and runs `suveryn-provision first-boot`. Its progress is shown on the machine's
screen and in the journal.

On success it:
- deletes `first-boot.env` and any private key from the EFI partition;
- leaves the local CA's certificate there (`suveryn-local-ca.crt`) for the office's workstations to trust;
- shows the temporary Keycloak admin password **on the machine's own screen only**, never in the journal.

On failure it says so on the screen and runs again at the next start, until it succeeds. Unknown
settings in `first-boot.env` stop it. It runs once (`/var/lib/suveryn/.first-boot-done`); after
that the services start on their own at every boot.

### The two network modes

| | connected, approved destinations (default) | connected, all outgoing traffic | air-gapped |
|---|---|---|---|
| LLM files | Downloaded, checked against the manifest's sha256 | Same | From media: a USB disk labelled `SUVERYN-MODELS` (mounted read-only), checked the same way |
| Outgoing traffic | The office's network, and the approved destinations (sūveryn, Hugging Face, Ubuntu's security updates, time, the office's connectors) | Anything | The office's own network only |
| Ubuntu security updates | Automatic (unattended-upgrades) | Same | Off: a signed update bundle on media (#40, not built yet) |
| Subscription manifest (Enterprise) | Checked locally by the web setup when the office gives one ([`support/`](../support/README.md)); it is never fetched | Same | Same |

ufw lets in only 80 and 443 in every mode. What goes out is enforced by the network mode's own
nftables table and resolver (`suveryn-egress`, `suveryn-dns`), loaded before the network comes up
at every boot; `verify` runs the outgoing-traffic check. See [`network-mode`](../network-mode/README.md).

Everything else is the same in all modes. The embedding and Docling models are in the image,
and the realm is built from the image's own suveryn-core checkout, so first boot needs no internet
access except, when connected, for the LLM files.

## Hardening

- **Minimal installation:** `ubuntu-server-minimal`. Snap, ModemManager, iSCSI, multipath, apport and
  the Ubuntu Pro client are removed; the CUDA compiler, build tools, Node and every cache go out after
  the build.
- **One door in, a short list out:** ufw allows only 80 (redirects to https) and 443 in; the network
  mode decides what goes out (above). PostgreSQL, Keycloak, llama-server
  and the gateway listen on loopback only. Nothing answers on 80/443 until first boot has written the
  office's site.
- **No SSH server and no login:** the build user and the SSH server are removed last, and root is
  locked. Remote support is decided for connected appliances only (decision D6, #43) and isn't built.
- **Kernel settings:** pointer and dmesg restrictions, BPF hardening, kexec disabled, ptrace limited
  to admins, no redirects or source routing.
- **No core dumps** (they would hold document text), and a persistent journal capped at 1 GB.
- **Identity:** every flashed box gets its own machine-id, certificates and secrets at first start.
- **Already provisioned:** the reader sandbox, the gateway's own hardening, and the proxy's security
  headers.

## Licences

The image redistributes NVIDIA's driver (Ubuntu's `linux-modules-nvidia-580-open-generic`,
`nvidia-utils-580`, `libnvidia-compute-580` and the `nvidia-firmware-580` package they need). That
is allowed (decided October 2026, development context §2, tracker #49): the open kernel modules are
MIT, and the NVIDIA Driver License Agreement (§1.1(d)) lets the driver be distributed for use with an
open-source kernel on two conditions, which every build keeps to:

- **The binaries are Canonical's packages, unmodified.** Nothing patches, re-signs or translates the
  driver's user-space libraries or firmware. (Signing the kernel modules would be fine, they are MIT;
  Canonical's are signed already.)
- **The licence text ships with the image.** In this build the packages' own
  `/usr/share/doc/*/copyright` files stay in place (`cleanup.sh` doesn't remove them). The mkosi
  build has no `/usr/share/doc` (`WithDocs=no`), so the text is in
  `mkosi/mkosi.extra/usr/share/suveryn/licences/`, and `mkosi.postinst.chroot` stops a build that has
  the driver without it.

The licence is between the office and NVIDIA. For GeForce cards it only covers hardware the office
owns, outside a datacenter (development context §2); that is a condition on the office's use, not on
this image.

## Testing

**Built** on a RunPod pod without KVM, with software emulation (`accelerator=tcg`).

**Not yet tested:** booting the image on real hardware with a GPU. That needs a bare-metal server:
write the image to its disk from a rescue system
(`zstd -dc suveryn-appliance.img.zst | dd of=/dev/nvme0n1 bs=4M conv=fsync`), put `first-boot.env`
on its EFI partition, boot it, and check that `suveryn-provision verify` passes, as it did on the
dev pod.

## Open

- **Remote support:** decided for connected appliances only, never air-gapped (decision D6, #43;
  [`docs/design/appliance-architecture.md`](../docs/design/appliance-architecture.md)). Not built:
  none in the image.
- **Updates for offline installations:** a signed bundle on media, tied to the signing decisions
  (development context §8, §11).
- **`first-boot.env`:** the web setup ([`setup`](../setup/README.md)) replaces it once the real
  provisioning through the web setup (#37 part 6b) has passed on a machine.
