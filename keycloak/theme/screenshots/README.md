# Screenshots of the login theme

`shoot.py` starts Keycloak with the built theme and a throwaway realm, walks the eight pages a notary can
meet and writes a PNG per page, at 1440 and 390 px wide, in Dutch: sign in, a wrong password, forgot
password, the e-mail notice, the two-step code, setting up the authenticator, a new password, and the
error page. They are the "code" side of the design boards for these pages (the canvas *Sūveryn ·
interface*, page *Aanmelden*, boards A01 to A08), the way suveryn-core's screen previews are for the chat
UI: a change to the theme is checked against its board here before it is merged (the handbook's decision
of 6 November 2026).

```bash
uv run keycloak/theme/build_theme.py                                   # the theme first
uv run --no-project --with playwright playwright install chromium       # once
uv run --no-project --with playwright --with aiosmtpd keycloak/theme/screenshots/shoot.py
```

Needs Java 21. Keycloak 26.8.0 is downloaded once into `.keycloak/` here (git-ignored); `KEYCLOAK_HOME`
points at an unpacked Keycloak instead, `KEYCLOAK_VERSION` picks another release. The pages land in
`out/` (git-ignored too): the comparison is made by the design chat, not stored here.

`screenshots-realm.json` is the realm it imports: the theme, three languages with Dutch as the default,
password reset on, a public client for the sign-in flow, SMTP pointed at a stand-in on port 2525, and four
users with the password `Welkom-bij-suveryn-1`: `notaris` (plain), `nieuw` (must set a new password),
`tweestaps` (has an authenticator; secret `JBSWY3DPEHPK3PXP`) and `instellen` (must set one up). Nothing
in it is real, and nothing in it goes near an appliance.
