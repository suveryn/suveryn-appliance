# The legacy Packer test image

> **For testing with fictional documents only. Never an office's records.** This image has no
> encrypted data partition: whoever has the disk can read the documents, conversations and accounts on
> it. The appliance is the mkosi image in [`image/mkosi`](../mkosi/README.md), which also says when this one
> is retired.

A bare-metal disk image, for a test machine. Packer and QEMU are only the build tools: Ubuntu 24.04
is installed into a disk file inside a QEMU machine, provisioned and hardened there, and that disk
file is the image.

```bash
cd image/packer
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
| Services | PostgreSQL 16 + pgvector; suveryn-core with its embedding and Docling models; llama-server (llama.cpp, CUDA for Ampere, Ada, Hopper and Blackwell, and the A100); Keycloak with the theme; the reader sandbox; the chat UI; nginx with the header snippets | Database roles and passwords; the LLM files; Keycloak's host name and the production realm with a fresh client secret, built from the image's suveryn-core checkout (no network needed); TLS; the nginx site; the services started and `verify` run |
| Settings | None | Network mode, firewall and updates per mode (below) |
| Secrets | **None in the image**: no passwords, keys, certificates or machine-id | All generated on the box |

## First boot

**The web setup** (suveryn-tracker#37): when no `first-boot.env` is on the EFI partition, the first
start opens `https://<name>.local/` and someone on the office's network answers the setup screens;
[`setup`](../../setup/README.md) then runs `suveryn-provision first-boot` with those answers. Nothing the
office sets touches the EFI partition. `first-boot.env` below is the earlier way. It stays in this
test image until the web setup's real provisioning (#37 part 6b) has passed end to end. What has been
tested so far is in [`setup/README.md`](../../setup/README.md#testing): the screens in a browser with a
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
PACKS_FROM=/media/suveryn-packs  # optional: the packs of a suveryn-packs checkout, unsigned (a test machine only)
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
| Subscription manifest (Enterprise) | Checked locally by the web setup when the office gives one ([`support/`](../../support/README.md)); it is never fetched | Same | Same |

ufw lets in only 80 and 443 in every mode. What goes out is enforced by the network mode's own
nftables table and resolver (`suveryn-egress`, `suveryn-dns`), loaded before the network comes up
at every boot; `verify` runs the outgoing-traffic check. See [`network-mode`](../../network-mode/README.md).

Everything else is the same in all modes. The embedding and Docling models are in the image,
and the realm is built from the image's own suveryn-core checkout, so first boot needs no internet
access except, when connected, for the LLM files.

## Hardening

This image has the same hardening settings as the appliance image
([`image/mkosi`, *Hardening*](../mkosi/README.md#hardening)): the kernel settings, the core-dump limits, the
1 GB journal, the services on loopback and the identity. In addition, Packer only:

- **Minimal installation:** `ubuntu-server-minimal`. Snap, ModemManager, iSCSI, multipath, apport and
  the Ubuntu Pro client are removed; the CUDA compiler, build tools, Node and every cache go out after
  the build.
- **ufw** lets in only 80 (redirects to https) and 443. Nothing answers on them until first boot has
  written the office's site.
- **No SSH server and no login:** the build user and the SSH server are removed last, and root is
  locked. Remote support isn't built: the design note's
  [*Remote support*](../../docs/design/appliance-architecture.md#remote-support-43-connected-only) says what is decided.

## Licences

NVIDIA's driver ships on the two conditions the handbook's development context §2 sets (tracker #49).
In this image the licence text is the packages' own `/usr/share/doc/*/copyright` files, which
`cleanup.sh` keeps; the mkosi image keeps it in
[`licences/`](../mkosi/mkosi.extra/usr/share/suveryn/licences/README.md).

## Testing

**Built** on a RunPod pod without KVM, with software emulation (`accelerator=tcg`).

**Not yet tested:** booting the image on real hardware with a GPU. That needs a bare-metal server:
write the image to its disk from a rescue system
(`zstd -dc suveryn-appliance.img.zst | dd of=/dev/nvme0n1 bs=4M conv=fsync`), put `first-boot.env`
on its EFI partition, boot it, and check that `suveryn-provision verify` passes, as it did on the
dev pod.
