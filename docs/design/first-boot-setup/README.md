# First-boot setup: design handoff

Design for the sūveryn appliance's bare-metal console installer and its web setup wizard. Approved October 2026. Tracker: see the implementation issue that links here. Background: §5.8 (setup flow) and §8 (licensing and support) of the development context.

## What is in this folder

| Path | What it is |
|---|---|
| `screens/*.html` | Static HTML of every screen, in its default state plus the important variants (`--variant` in the name). Open them in a browser; links between screens work. |
| `screenshots/*.png` | The same screens as images (web screens full page at 1440 px wide, console screens at 1280 × 800). |
| `assets/` | Self-hosted brand fonts (Inter, Space Grotesk, IBM Plex Mono) and the sūveryn icon used by the screens. |
| `index.html` | Overview of all screens with thumbnails. |

**These files are the visual and copy reference, not production code.** Rebuild the web setup in the `chat-ui` stack (React, TypeScript, Vite) with tokens from `suveryn-brand` via `brand-lock.json`, and build the console installer as a text UI. Copy (wording) in the screens is final for English unless a note below says otherwise.

## Flow

### 1. Console installer (bare metal, from the USB stick)

| # | Screen | File |
|---|---|---|
| C1 | Language, keyboard, time zone | `console-01-language-keyboard.html` |
| C2 | Hardware check and target disk | `console-02-hardware-disk.html` |
| C3 | Language models from the stick | `console-03-models.html` |
| C4 | Network: name and DHCP | `console-04-network.html` |
| C4b | Network: no DHCP, fixed address | `console-04b-network-no-dhcp.html` |
| C5 | Ready to install (type ERASE) | `console-05-summary.html` |
| C6 | Installing | `console-06-installing.html` |
| C7 | Disk recovery key (shown once) | `console-07-recovery-key.html` |
| C8 | Console after first boot (waiting for setup / setup complete) | `console-08-after-boot*.html` |
| C8b | Boot: disk won't unlock | `console-08b-disk-unlock.html` |

### 2. Web setup (from another computer, `https://<name>.local`)

| # | Screen | Files |
|---|---|---|
| 0 | Welcome | `web-00-welcome.html` |
| 1 | Network: Connected (approved destinations or allow all) or Air-gapped | `web-01-network*.html` |
| 2 | Administrator account | `web-02-administrator.html` |
| 3 | Certificate: self-signed or own | `web-03-certificate*.html` |
| 4 | Support agreement: register by QR code, upload manifest | `web-04-support-agreement*.html` |
| 5 | Backups: USB disk, network share or later; retention; recovery key | `web-05-backups*.html` |
| 6 | Review | `web-06-review.html` |
| 7 | Provisioning | `web-07-provisioning.html` |
| 8 | Complete (outgoing-traffic check passed / failed) | `web-08-complete*.html` |

## Decisions to implement

**Console installer**
- Linux VT text UI, 16 colours set with `setvtrgb` from the dark brand theme. Font: Terminus (needs `ū`). The mock-ups use Source Code Pro as a stand-in.
- Network name is set in the installer and announced over mDNS (`<name>.local`). DHCP first; after 30 s without a lease, offer a fixed address (C4b).
- Model bundles come from the stick's exFAT partition `suveryn-bundles` (file system label `SUVBUNDLES`: exFAT labels hold 11 characters), which fills the rest of the stick; C3 names it by its partition name. Each bundle's signed manifest is checked against the bundle public key built into the image, then every file's SHA-256 (tracker #36). Several models can be installed if the hardware allows.
- Disk: LUKS2 with a new key per machine, unlocked by TPM 2.0 (PCR 7). Recovery key in modhex (systemd recovery key format), shown once (C7) and confirmed by typing the first group. Unlock prompt allows 5 attempts (`crypttab tries=5`). Tracker #34.
- Secure Boot is not required (customer hardware).
- Installation creates the appliance identity: UUID and Ed25519 key pair (tracker #35).
- C8 shows addresses, the certificate fingerprint, appliance ID and support status. Menu option 3 shows the registration QR code in text mode (block characters, inverted colours, e.g. `qrencode -t ANSIUTF8`).

**Web setup**
- Reachable on the local network only until setup finishes, then closes itself. Cannot depend on Keycloak (it sets Keycloak up).
- Layout aligned with the Keycloak login: lockup and tagline above a 560 px card, language dropdown in the card's top-right corner (English, Nederlands, Français). Focus ring: border `#0F8A82` plus a 3 px `rgba(58,212,202,.15)` glow.
- Network: Connected (default) with "Approved destinations only" (default) or "Allow all outgoing traffic", or Air-gapped. The appliance's own firewall enforces it, and provisioning checks it last (screen 8 shows the result).
- Administrator password: at least 15 characters, not a common or leaked password, not the name, email or appliance name; show/hide toggle; no checklist on screen, errors only on Continue. Keycloak's password policy must enforce the same rules.
- Support agreement (tracker #35): shows appliance ID and key fingerprint, a QR code with a portal link (no app needed), and a manifest upload. Optional: Continue works without one. Without a valid manifest: admin-only reminder for 14 days, then a banner for everyone who signs in. Every deviation from the manifest (users over contract, uncertified connectors or third-party playbooks, missed quarterly check-in, expiry) is shown to everyone. Nothing is ever blocked.
- Backups (tracker #33): USB disk (pick the detected disk) or network share (SMB or NFS, address, user, password, Test connection), or Set up later with a warning. Retention 14 days, 30 days (default, proposed), 90 days, 1 year. Backups are encrypted to a recovery key generated in the browser; only the public half goes to the appliance; the admin must confirm storing it offline.
- Conversation retention and the "what deleting means" explanation (tracker #31) are in the admin dashboard, not in setup.

**Copy and brand rules**
- UK English. Dutch formal (u/uw). The product name is lower case "sūveryn" except at the start of a sentence; the company is "Sūveryn Labs". Say "the appliance" or "this appliance" when the appliance is meant, never "sūveryn" alone.
- No em dashes in UI copy. Errors use accent ink `#C42F00` with an icon, never a separate red. Lucide icons only.

## Resolved while building

- **Setup check of 21 October 2026** (the console screens and the web setup against these mock-ups):
  - Console: the bars are one unbroken #12161B band and the window is filled with it, with its border in
    #757E86 (bright black in the palette, or black made bright by bold on the Linux console); a blank line
    under each title; a radio list's highlight is as wide as the fields, its marks in accent; a text
    field's "]" lines up with a dropdown's, its field grey also when empty; a field's brackets are in
    accent while it has the focus; the recovery key is centred in an accent frame, its match on the
    field's own line, and *Restart* shows as what Enter does; French punctuation after a space never
    starts a line.
  - C8 after boot stays a centred window, not the full-width layout (decided by the owner).
  - Web: the warning under "Allow all outgoing traffic" and the note for air-gapped; the appliance ID and
    the short fingerprint on one line each, the QR code at 148 px; the ✓ on a loaded manifest; the
    welcome's narrower intro. The setup stays light only (decided by the owner; there are no dark
    mock-ups).
  - As expected: `√` for `✓` (Terminus has no ✓) and bold as bright white on the Linux console.
- **Pod check of f0ff96c** (348 console screens):
  - C8 fits 80×25 in Dutch and French: a shorter certificate line on a console under 30 lines, and the
    label column as wide as its longest label (also on the QR screen, "Vingerafdruk van de sleutel").
  - C8b, the disk unlock in the initrd, as designed: the bars full width, the keys in the bottom bar,
    the text wrapped at word boundaries to the console's width (no blank lines on a 25-line console),
    the field's brackets in accent. Its top bar has no host name: that is on the locked disk.
  - A radio option that wraps keeps its accent mark and continues under its own text; C4's Address
    options side by side, as designed; a number stays with its unit ("1 Gbit/s"), and a "·"
    separator never ends or starts a line.
  - The help is a box of its own, with its title on its border, as wide as the window's inside, so no
    word of the screen beneath shows cut off beside it.
  - A screen without buttons (C6) has a blank line before its bottom border.

## Not in this design

- Admin dashboard screens (check-in QR, warnings, conversation retention, maintenance).
- The banners shown to users (no valid manifest, deviations, expiry).
- Dutch and French copy.
- The portal.

## Open points

- Default backup retention (30 days is a proposal; #31).
- Renewal discount (commercial; no screen impact).
