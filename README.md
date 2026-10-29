# suveryn-appliance

Website: [suveryn.com](https://www.suveryn.com)

OS image build, hardening and first-boot provisioning for sūveryn appliances, including the runtime services [suveryn-core](https://github.com/suveryn/suveryn-core) depends on but doesn't bundle: PostgreSQL with pgvector, Keycloak and the model server. Licensed under Apache-2.0 ([LICENSE](LICENSE)).

| Folder | Contents |
|---|---|
| [`keycloak/theme`](keycloak/theme/README.md) | The sūveryn Keycloak theme: login pages, account console and emails |
| [`keycloak/realm`](keycloak/realm/README.md) | The production realm, built from suveryn-core's development realm, and Keycloak's production configuration |
| [`reader-sandbox`](reader-sandbox/README.md) | Runs suveryn-core's PDF reader as its own user, without network, under seccomp |
| [`network-mode`](network-mode/README.md) | The installation's network mode (`connected`, with approved destinations only or all outgoing traffic, or `air-gapped`), in `/etc/suveryn/network-mode.env`, readable by any component and enforced by the appliance's own outgoing rules and resolver; with the outgoing-traffic check |
| [`reverse-proxy`](reverse-proxy/README.md) | nginx: one https origin for the chat UI, the gateway and Keycloak; TLS 1.2/1.3; strict security headers |
| [`image`](image/README.md) | The appliance image: the read-only system with its encrypted data partition ([`image/mkosi`](image/mkosi/README.md)), the one offices get; and the older Packer build, unencrypted and for testing with fictional documents only, until it is retired |
| [`image/mkosi`](image/mkosi/README.md) | The read-only, verified system of the approved design (mkosi): slot A with dm-verity, the boot file, the disk's partition definitions, `/var` on the data partition; tested in a virtual machine |
| [`bundles`](bundles/README.md) | Signed bundles (models now, system updates later): `suveryn-bundle-release` makes and signs them at Sūveryn Labs (AWS KMS); the appliance checks them with `/usr/lib/suveryn/bundle` |
| [`setup`](setup/README.md) | The web setup on the appliance's side: answers the setup screens (suveryn-core `setup.html`) on the first start, checks and keeps the office's answers, runs provisioning with them and closes setup |
| [`support`](support/README.md) | Support manifests: Sūveryn Labs' signed statement of an appliance's support agreement, checked on the appliance |
| [`provision`](provision/README.md) | `suveryn-provision`: installs and wires up PostgreSQL/pgvector, Keycloak (theme and production realm), llama-server and its models, suveryn-core and the reader sandbox on Ubuntu 26.04 (or 24.04 until #57), then verifies them end to end |
| [`backup`](backup/README.md) | `suveryn-backup`: the nightly backups, encrypted for the office's recovery key, onto a USB disk or a share; the administrator's key test and requests; restoring onto a replacement machine |
| [`docs/design`](docs/design/appliance-architecture.md) | Design notes: the appliance's architecture (read-only system, data partition, updates, decisions) and the [first-boot setup](docs/design/first-boot-setup/README.md) |

Related: [suveryn-docs](https://github.com/suveryn/suveryn-docs) (installation and administration), [suveryn-brand](https://github.com/suveryn/suveryn-brand) (the mark, fonts and tokens; the mark is not covered by this repository's licence and is fetched at build time).
