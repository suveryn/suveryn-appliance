# Sūveryn Keycloak theme

The branded sign-in for sūveryn appliances: Keycloak's **login** pages, **account console** and **emails**, in the sūveryn light theme. The **admin console** is deliberately left as Keycloak ships it: only IT admins see it, and theming it risks breaking on every Keycloak upgrade.

It is a standard Keycloak theme (FreeMarker templates, CSS, message bundles) that **extends Keycloak's own themes** instead of changing Keycloak:

| Part | Extends (Keycloak 26.8) | What differs |
|---|---|---|
| `login` | `keycloak.v2` | `template.ftl`: the page frame (logo, tagline, optional "Air-gapped" badge, centred card), copied from keycloak.v2's with only the frame replaced. `login.ftl`: one line under the password field. Styles, Lucide eye icons for show/hide password and the Lucide languages icon before the language menu (as in the chat UI), messages. Every other page (TOTP, password update, errors, …) is Keycloak's, inside the sūveryn frame |
| `account` | `keycloak.v3` | Through its supported settings only: logo, favicon, title, stylesheet, messages. The stylesheet gives it the sign-in's fields (hairline border, 6px corners, teal focus ring, no PatternFly underline), sizes (fields and buttons 44px, the masthead's user menu 32px), the brand's colours for link and secondary buttons and the current menu item, and hairline dividers in the sidebar |
| `email` | `keycloak` | `html/template.ftl`: the HTML frame, headed by the lockup (the brand icon, inlined as SVG, next to the wordmark as text; clients that strip SVG, such as Gmail and Outlook for Windows, show the wordmark alone). Messages. Plain-text emails are Keycloak's |

Because only the frame and one line are overridden, a Keycloak upgrade keeps working. After an upgrade, compare `src/suveryn/login/template.ftl` and `login.ftl` with the new `keycloak.v2` versions (the header comment in each says which version they come from).

## Build

```bash
uv run keycloak/theme/build_theme.py
```

This writes `keycloak/theme/build/theme/suveryn/` (a theme folder) and `keycloak/theme/build/suveryn-keycloak-theme.jar` (the same, for Keycloak's `providers/` folder). It:

1. fetches the brand assets from [suveryn-brand](https://github.com/suveryn/suveryn-brand) at the commit pinned in [`brand-lock.json`](brand-lock.json), and checks each file's SHA-256. The sūveryn mark is all rights reserved, so it is fetched at build time and never committed to this Apache-2.0 repository. To build without internet access, point `SUVERYN_BRAND_REPO` at a local mirror. To move to a newer brand commit: `--update <commit-or-tag>`.
2. crops the logotype's canvas (`logo/suveryn-logotype-light-bg.svg` has blank space to the right of the wordmark). The new viewBox is computed from the shapes and from IBM Plex Mono's own glyph metrics, and the shapes and text are untouched. The wordmark is converted to outlines (paths) from IBM Plex Mono 500, for both the login page (inlined) and the account console (an `<img>`), so neither needs a web font. As live text it flashed in a fallback font on every page load, such as each language switch (suveryn-tracker#13).
3. bundles the fonts (Inter, Space Grotesk; SIL OFL) with their licence texts, plus IBM Plex Mono's licence, since the wordmark's outlines come from it, and the Lucide licence in `THIRD-PARTY-NOTICES.txt`.

## Install

Copy `build/theme/suveryn` into Keycloak's `themes/` folder, or `build/suveryn-keycloak-theme.jar` into `providers/` and run `kc.sh build`. Then select it for the realm (*Realm settings → Themes*, or):

```bash
kcadm.sh update realms/suveryn -s loginTheme=suveryn -s accountTheme=suveryn -s emailTheme=suveryn
```

## Design rules it keeps

From the reviewed login mockup and the Sūveryn Design System:

- **Light theme only.** `darkMode=false`, so Keycloak's automatic dark mode is off.
- **Colours** are suveryn-brand's tokens (version 2): background `#FFFFFF`, card `#FAFAFA`, borders `rgba(2,2,5,0.12)`, text `#020205`, secondary text `rgba(2,2,5,0.62)`, helper text `rgba(2,2,5,0.55)`, accent `#FA5B35` (button fills), accent-ink `#C42F00` (links), teal-ink `#007F78` (the badge).
- **Contrast is measured the Design System's way, from OKLCH lightness** ([`tests/oklch_contrast.py`](tests/oklch_contrast.py)): luminance is taken as L³ and the WCAG ratio applied. All text meets 4.5:1 and UI boundaries 3:1, and `test_text_contrast_by_oklch` checks every pair. The mockup's link colour (the accent), helper text (0.40) and badge teal (`#0F8A82`) failed that test; suveryn-brand's tokens were corrected for it ([suveryn-brand#2](https://github.com/suveryn/suveryn-brand/pull/2)), and the theme uses the corrected values.
- **Teal only for system signals:** the "Air-gapped" badge, and the focus state of inputs (`border-color: #0F8A82`, a UI boundary that meets 3:1; `box-shadow: 0 0 0 3px rgba(58,212,202,0.15)`; no browser outline).
- **Fonts:** Space Grotesk 600 for the card heading, Inter 400/600 for everything else, and IBM Plex Mono 500 only for the wordmark, drawn as outlines so it is never shown in a fallback font. The fonts are bundled with the theme rather than loaded from Google Fonts: the login page must work air-gapped, and it must not tell a third party when someone signs in.
- **Layout:** centred card, max 560px wide (as the web setup's card), 14px corners; inputs and buttons have 6px corners. The logo is 48px high.
- **Sizes:** fields and buttons 44px high, the language menu 32px: the same heights as the web setup and the chat UI (suveryn-core `packages/chat-ui/src/ui/ui.css`, "sizes"), so the sign-in and the setup line up.
- **Tagline, verbatim, in every language:** "AI for work that can't leave the premises."
- **Notices** (wrong password, "you need to change your password", expired page) use one neutral style instead of PatternFly's amber, red, blue and green: the light surface, a hairline border, ink text. Warnings and errors add an accent-ink left bar and icon; info and success a quiet icon. Icons are Lucide. Field errors (border, message, icon) use accent-ink too: the brand has no separate error red, so an error is told apart by its icon and message.
- Under the password field: "Works with your office's existing directory account too." LDAP/AD users sign in with this same form; there is no separate "sign in with" button.

## The "Air-gapped" badge

The badge is the website hero's badge from [suveryn-website](https://github.com/suveryn/suveryn-website) `index.html`: the Lucide wifi-off icon (9×9, stroke-width 2.4), 10px/600 teal text, the teal wash and border, 20px radius and `3px 8px` padding, with text and icon in the brand's corrected teal-ink (`#007F78`; the website's `#0F8A82` measures 4.1:1 on the badge's wash).

It states a fact, so it shows **only when the installation's network mode is `air-gapped`** ([`network-mode`](../../network-mode/README.md)). Keycloak's unit loads `/etc/suveryn/network-mode.env`, and `theme.properties` reads the mode from that environment (`suverynNetworkMode=${env.SUVERYN_NETWORK_MODE:unknown}`, checked against Keycloak 26.8). When the file is missing or the value is anything else the property is `unknown` and the badge stays off. After changing the mode, restart Keycloak.

## Languages

`en`, `nl` and `fr`, switched with Keycloak's own language menu. The menu shows each language in its own name (English, Nederlands, Français), has a fixed width and sits in the card's top-right corner whatever the title, so it never moves when the language changes (suveryn-tracker#13).

- **English** is UK English. Keycloak's own English strings that use US spelling ("Organization", "Canceling", "Unauthorized") are overridden.
- **Dutch** is formal (*u/uw*). Keycloak's own Dutch strings are mostly formal and used as they are. The login theme adds the sūveryn strings and corrects three spelling errors. The account theme rewrites Keycloak's five informal account-console strings (*je*) and adds the two that Keycloak 26.8 hasn't translated, which would otherwise show in English (suveryn-tracker#9). A test fails if an informal form (*je/jouw/jij*) slips in.
- **French** has only the sūveryn strings; the rest is Keycloak's, which is complete and formal (*vous*).
- Every theme (login, account, email) ships `messages_en`, `messages_nl` and `messages_fr`, which a test checks.
- The tagline and the "Air-gapped" label stay in English in every language, as on the Dutch website.

## Tests

```bash
uv run --no-project --with pytest --with fonttools==4.66.1 --with defusedxml==0.7.1 pytest keycloak/theme/tests
```

The tests check the rules above in the sources: parent themes, light only, no admin theme, the tagline, the badge (off by default, website styling), no white on the accent, contrast of every text colour by OKLCH lightness, teal focus, nothing loaded from the internet, IBM Plex Mono only for the wordmark, formal Dutch, UK English, and the built output (crop, outlined logo, fonts, notices). `test_build.py` checks that the jar is byte-for-byte reproducible (fixed entry order, dates and permissions) and that `SUVERYN_BRAND_REPO` can't pass options to git; it needs fontTools, like the build.

Checked by hand against Keycloak 26.8.0 with the `suveryn` development realm from suveryn-core:
- **Login page** in English, Dutch and French, at 1200px and 375px.
- **Wrong password**, the **TOTP set-up page**, the password show/hide toggle, and the badge both on and off.
- **Account console.**
- **Emails** (test email, and "update your account" in English and Dutch) through a local mail catcher.
- Neither the login page nor the account console requested anything from another host.
