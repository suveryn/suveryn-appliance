# suveryn-appliance

OS image build, hardening and first-boot provisioning for sūveryn appliances, including the runtime services [suveryn-core](https://github.com/suveryn/suveryn-core) depends on but doesn't bundle: PostgreSQL with pgvector, Keycloak and the model server. Licensed under Apache-2.0 ([LICENSE](LICENSE)).

| Folder | Contents |
|---|---|
| [`keycloak/theme`](keycloak/theme/README.md) | The sūveryn Keycloak theme: login pages, account console and emails |
| [`keycloak/realm`](keycloak/realm/README.md) | The production realm, built from suveryn-core's development realm, and Keycloak's production configuration |
| [`reader-sandbox`](reader-sandbox/README.md) | Runs suveryn-core's PDF reader as its own user, without network, under seccomp |

Related: [suveryn-docs](https://github.com/suveryn/suveryn-docs) (installation and administration), [suveryn-brand](https://github.com/suveryn/suveryn-brand) (the mark, fonts and tokens; the mark is not covered by this repository's licence and is fetched at build time).
