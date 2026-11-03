# suveryn-appliance

The machine in the office: the OS image, its hardening, the installer, first-boot setup and provisioning, and the runtime services [suveryn-core](https://github.com/suveryn/suveryn-core) depends on but doesn't bundle (PostgreSQL with pgvector, Keycloak, llama-server). Apache-2.0. The brand mark is not under this licence and is fetched at build time.

Start with the private [suveryn-handbook](https://github.com/suveryn/suveryn-handbook/blob/main/context/development-context.md#start-here), its *Start here*: the design principles, the project's words, and how a change reaches `main`; then [docs/design/appliance-architecture.md](docs/design/appliance-architecture.md): the disk, the boot and what "verified" means, the installer, first boot, updates, backups, the clock, remote support, testing, the build order and decisions D1 to D9. The first-boot screens are in [docs/design/first-boot-setup](docs/design/first-boot-setup/README.md). Each folder has a `README.md`; the table in [README.md](README.md) maps them.

## Rules

- **The spec changes in the same PR as the code**, and so does the folder's README. Cross-repository and commercial decisions go to the handbook.
- **One branch and PR per change, named for its tracker issue** (`suveryn-tracker#n`). Merge only when the founder says so. Never push to `main`; never change repository settings, secrets or CI. A coding agent never pushes: it delivers a patch, applied in a separate worktree, as *Start here* says.
- **Core is pinned by commit** in `provision/versions.json`, with every other download pinned by sha256 or commit. The pin moves in its own "Pin suveryn-core at …" PR, after a VM regression against that commit has passed and its findings are fixed in both repositories.
- **Both network modes must work**: Connected (approved destinations only, or allow all) and Air-gapped. Nothing may fetch at runtime what wasn't put on the image or in a signed bundle. The mode is in `/etc/suveryn/network-mode.env`, enforced by the appliance's own rules and resolver.
- **The system is read-only and verified; `/var` is the encrypted data partition.** Anything that must persist goes on the data partition; `/etc` is links. Ubuntu fixes arrive only through sūveryn updates (D3); no apt on the appliance; no swap (D5).
- **Bundles are signed and checked before anything is written**: models now, updates later, one verification path. Signing happens at Sūveryn Labs (AWS KMS); the appliance holds public keys only.
- **Nothing is ever blocked by the support agreement** (decision, 28 October 2026): not updates, not model bundles. Deviations show a notice to everyone who signs in.
- **The web setup asks for no office settings**; it provisions fail-safe defaults and closes itself when done. Office settings live in core's Administration.
- **The reader sandbox is mandatory in production**: core refuses to read documents without `SUVERYN_EXTRACT_WRAPPER` unless told it is a development machine.
- **It keeps working by itself** (principle 4). After a restart of any service, a reboot or a full disk, the appliance comes back without a hand; an administrator sees what is wrong on Status, never only in a journal; a backup counts once the key test or a restore has shown it opens. A change to a unit, a service or a script says how it recovers, and its folder's tests restart it.
- **Screens meet WCAG 2.2 AA** (the web setup, the sign-in and account pages, the console installer): keyboard, focus, contrast, a name for every control. Screen readers are not tested for now.
- **Copy** (Keycloak theme, installer, setup) follows the brand's [Wording and spelling](https://github.com/suveryn/suveryn-brand/blob/main/guidelines/brand-guidelines.md#wording-and-spelling); the brand's tokens and type scale come through the same pinning as core's chat UI, and a pixel literal fails a test. Each language has its own messages file, except the console installer's `installer-text.json`, which keeps the languages side by side because the installer's small reader needs one file.

## Verify

Each folder has `tests/`; run them with `python3 -m pytest <folder>/tests` (some need PostgreSQL, age or openssl and say so at the top of the file, skipping without). CI runs shellcheck on every script, validates the pinned-version and lock files, and checks the mkosi configuration (`mkosi --directory image/mkosi summary`); the image itself is built and tested in a VM with a virtual TPM as [appliance-architecture.md](docs/design/appliance-architecture.md) *Testing* describes, and has not yet run on real hardware with the GPU (#47, #48).
