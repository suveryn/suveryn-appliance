# suveryn-appliance

OS image build, hardening and first-boot provisioning for sūveryn appliances, including the runtime services [suveryn-core](https://github.com/suveryn/suveryn-core) depends on but doesn't bundle: PostgreSQL with pgvector, Keycloak and the model server. Licensed under Apache-2.0 ([LICENSE](LICENSE)).

| Folder | Contents |
|---|---|
| [`keycloak/theme`](keycloak/theme/README.md) | The sūveryn Keycloak theme: login pages, account console and emails |
| [`keycloak/realm`](keycloak/realm/README.md) | The production realm, built from suveryn-core's development realm, and Keycloak's production configuration |
| [`reader-sandbox`](reader-sandbox/README.md) | Runs suveryn-core's PDF reader as its own user, without network, under seccomp |
| [`network-mode`](network-mode/README.md) | The installation's network mode (`connected`, with approved destinations only or all outgoing traffic, or `air-gapped`), in `/etc/suveryn/network-mode.env`, readable by any component and enforced by the appliance's own outgoing rules and resolver; with the outgoing-traffic check |
| [`reverse-proxy`](reverse-proxy/README.md) | nginx: one https origin for the chat UI, the gateway and Keycloak; TLS 1.2/1.3; strict security headers |
| [`image`](image/README.md) | The bare-metal appliance image (Packer/QEMU as build tools): what is built in, what happens at first boot per network mode, hardening |
| [`provision`](provision/README.md) | `suveryn-provision`: installs and wires up PostgreSQL/pgvector, Keycloak (theme and production realm), llama-server and its models, suveryn-core and the reader sandbox on Ubuntu 24.04, then verifies them end to end |

Related: [suveryn-docs](https://github.com/suveryn/suveryn-docs) (installation and administration), [suveryn-brand](https://github.com/suveryn/suveryn-brand) (the mark, fonts and tokens; the mark is not covered by this repository's licence and is fetched at build time).
