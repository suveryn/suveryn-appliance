# suveryn-appliance

The machine in the office: the OS image, its hardening, the installer, first-boot setup and provisioning, and the runtime services [suveryn-core](https://github.com/suveryn/suveryn-core) depends on but doesn't bundle (PostgreSQL with pgvector, Keycloak, llama-server). Apache-2.0. The brand mark is not under this licence and is fetched at build time.

Start with the private [suveryn-handbook](https://github.com/suveryn/suveryn-handbook/blob/main/context/development-context.md#start-here), its *Start here*: the design principles, the project's words, and how a change reaches `main`; then [docs/design/appliance-architecture.md](docs/design/appliance-architecture.md): the disk, the boot and what "verified" means, the installer, first boot, updates, backups, the clock, remote support, testing, the build order and decisions D1 to D10. The first-boot screens are in [docs/design/first-boot-setup](docs/design/first-boot-setup/README.md). Each folder has a `README.md`; the table in [README.md](README.md) maps them.

## Before you start

<!-- shared: copied from suveryn-handbook/context/claude-md-shared.md; change it there, never here -->

1. **Read the handbook's [*Start here*](https://github.com/suveryn/suveryn-handbook/blob/main/context/development-context.md#start-here)** before any work: the design principles, the project's words, how a change reaches `main`. If you can't open it, stop and say so. In a long session, read it again when the conversation has been summarised.
2. **One issue, one branch, one PR.** The change has an issue in suveryn-tracker and the branch is named for it. No branch is built on an unmerged branch. A coding agent never pushes: it delivers its commits as a branch fetched from a git bundle into the founder's local clone, never switching the branch that clone or another session is on, and checks the result is identical to what it built. Before it is delivered, a fresh session that did not build it reviews it ([the reviewer's brief](https://github.com/suveryn/suveryn-handbook/blob/main/context/review-brief.md)). Only the founder pushes and merges.
3. **Before you ask for a merge**, say which design principle the change bends, if any, and why. A bent principle needs the founder's decision in the handbook before the merge.

<!-- /shared -->

## Rules

The handbook's [design principles](https://github.com/suveryn/suveryn-handbook/blob/main/context/development-context.md#the-design-principles) and its *How a change reaches `main`* hold here and are not repeated (a folder's README changes with its code, like a spec). These are the appliance's own rules:

- **Core is pinned by commit** in `provision/versions.json`, with every other download pinned by sha256 or commit. When and how the pin moves is the handbook's [*How a change reaches `main`*](https://github.com/suveryn/suveryn-handbook/blob/main/context/development-context.md#how-a-change-reaches-main), item 5; a PR that only moves it is titled "Pin suveryn-core at …".
- **Both network modes must work**: Connected (approved destinations only, or allow all) and Air-gapped. Nothing may fetch at runtime what wasn't put on the image or in a signed bundle. The mode is in `/etc/suveryn/network-mode.env`, enforced by the appliance's own rules and resolver.
- **The system is read-only and verified; `/var` is the encrypted data partition.** Anything that must persist goes on the data partition; `/etc` is links. Ubuntu fixes arrive only through sūveryn updates (D3); no apt on the appliance; no swap (D5).
- **Bundles are signed and checked before anything is written**: models now, updates later, one verification path. Signing happens at Sūveryn Labs (AWS KMS); the appliance holds public keys only.
- **The web setup asks for no office settings**; it provisions fail-safe defaults and closes itself when done. Office settings live in core's Administration.
- **The reader sandbox is mandatory in production**: core refuses to read documents without `SUVERYN_EXTRACT_WRAPPER` unless told it is a development machine.
- **A unit, a service or a script says how it comes back** after a restart, a reboot or a full disk (`Restart=`, ordering, what Status shows), and its folder's tests restart it.
- **The accessibility principle covers the appliance's screens too**: the web setup, the sign-in and account pages, the console installer.
- **Copy** (Keycloak theme, installer, setup) follows the brand's [Wording and spelling](https://github.com/suveryn/suveryn-brand/blob/main/guidelines/brand-guidelines.md#wording-and-spelling); the brand's tokens and type scale come through the same pinning as core's chat UI, and a pixel literal fails a test. Each language has its own messages file, except the console installer's `installer-text.json`, which keeps the languages side by side because the installer's small reader needs one file. What a service or the console says when something is refused or fails is a code of suveryn-core's registry, never a word of its own ([`messages`](messages/README.md)).

## Verify

Each folder with code has `tests/` (also `keycloak/realm/tests/` and `keycloak/theme/tests/`; `image/mkosi/test/`; support's manifest tool is tested in `setup/tests/`); run them with `python3 -m pytest <folder>/tests` (some need PostgreSQL, age or openssl and say so at the top of the file, skipping without). The web setup's tests, the tests in `messages/` and provision's presets tests need a suveryn-core checkout beside this one (or `SUVERYN_CORE`), since the appliance's services say core's messages and keep no list of their own ([`messages`](messages/README.md)), and llama-server's presets are written with core's script; CI fetches core at the pinned commit for them. CI runs shellcheck on every script, validates the pinned-version and lock files, and checks the mkosi configuration (`mkosi --directory image/mkosi summary`); the image itself is built and tested in a VM with a virtual TPM as [appliance-architecture.md](docs/design/appliance-architecture.md) *Testing* describes, and has not yet run on real hardware with the GPU (#47, #48).
