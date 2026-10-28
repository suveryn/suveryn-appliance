# Production realm

The appliance's Keycloak realm, built from suveryn-core's development realm (suveryn-tracker#16).
One source: [`suveryn-realm.json`](https://github.com/suveryn/suveryn-core/blob/main/packages/api-gateway/keycloak/suveryn-realm.json)
in suveryn-core defines the client, its flow, the roles and the password and brute-force rules.
`build_realm.py` takes it at the commit in `realm-lock.json` (checked by SHA-256) and lays the
production settings over it.

```bash
python3 keycloak/realm/build_realm.py --public-url https://suveryn.office.example --out /root/suveryn-realm
kc.sh import --file /root/suveryn-realm/suveryn-realm.json   # or the admin console; then delete the file
```

It writes two files, both 0600 and git-ignored here:
- `suveryn-realm.json`: to import once, then delete, because it holds the client secret (and, with
  `--administrator FILE`, the office's administrator from the web setup);
- `gateway-oidc.env`: `SUVERYN_OIDC_ISSUER`, `SUVERYN_OIDC_CLIENT_ID`, `SUVERYN_OIDC_CLIENT_SECRET` and
  `SUVERYN_PUBLIC_URL` for the gateway's environment file; and, when suveryn-core's realm has the
  `suveryn-directory` client (cases, suveryn-tracker#10), `SUVERYN_DIRECTORY_CLIENT_ID` and a fresh
  `SUVERYN_DIRECTORY_CLIENT_SECRET` for it.

The `suveryn-directory` client is the gateway's read-only view of the office's accounts, so a case's
manager can add colleagues by name, and to know who administers (the badge on their avatar, suveryn-core
`docs/design/avatars.md`): a service account whose only roles are `view-users` and `view-realm`, both
read-only, no sign-in flow, no redirect URIs. The build refuses a realm where that service account has any other role,
where the chat client has a service account, or where the directory client could sign in.

## What production changes

| Setting | Development | Production | Why |
|---|---|---|---|
| `sslRequired` | `external` | `all` | `external` lets private addresses use plain HTTP, which in an office means every workstation |
| Sign-in events | off | on, kept 180 days | Failed sign-ins, lock-outs and logins can be investigated; username, IP and time only |
| Admin events | off | on, kept 180 days, no details | Who changed which user, role or client; not the changed data itself |
| Redirect, base and logout URLs | `http://localhost:5173/…` | exactly `<public-url>/…` | No wildcards: a wildcard lets a crafted link send the sign-in code elsewhere |
| Themes | Keycloak's | `suveryn` (login, account, email) | This repository's [theme](../theme/README.md) |
| Client secrets | none in the file | new random ones per build (`suveryn-chat`, and `suveryn-directory` when present) | Rotation: build again and re-import the clients |
| Password policy | at least 12 characters, not the user name or email | at least **15**, not the user name or email | The web setup's rule for the administrator (#37), for every account. The leaked-password list (`passwordBlacklist`) joins once it is on the appliance |
| The `suveryn-admin` role | as suveryn-core's realm | the same, checked: it may carry `manage-users`, `view-users`, `view-realm` and `view-events` (realm-management) and nothing more, and no other realm role may carry any | Administration › Users manages the accounts with the administrator's own token (suveryn-core `keycloak/README.md`): no extra client or secret, and the admin events name the administrator. An existing appliance gets the role's composites at its next realm import |
| The office's administrator | none | with `--administrator FILE` (`{name, email, password}`, from the web setup): signs in with the email address; realm role `suveryn-admin` and user management in the admin console (`manage-users`, `view-users`, `query-users`, `query-groups`), nothing on the realm's settings | Made by the realm import, so no password passes through Keycloak's admin API. The file holds only a pbkdf2-sha512 hash (210,000 iterations), which Keycloak 26 reads; checked by signing in with Keycloak 26.8 |

Everything else comes unchanged from the development realm:
- the authorisation-code flow with PKCE S256 only;
- no password grant;
- brute-force protection;
- no self-registration and no external identity providers.

`build_realm.py` refuses to build if the development realm ever loses one of these, or the password
policy drops below 15 characters.

Event retention is 180 days, decided on 2026-10-11. Long enough to investigate an incident found months later, short enough for data minimisation. Keycloak deletes older events itself: sign-in events through the realm's `eventsExpiration`, admin events through its `adminEventsExpiration` attribute, which Keycloak 26.8's event store reads. Keycloak's own log (the `jboss-logging` listener) goes to the journal, whose retention the appliance sets.

## Keycloak itself

[`../keycloak.conf.example`](../keycloak.conf.example) is the production configuration:
- a fixed `hostname` (`https://<office>/idp`), so issuer and redirects never come from request headers;
- `proxy-headers=xforwarded` behind the appliance's TLS proxy, listening on loopback only;
- PostgreSQL, with the password from the environment, never from the file;
- health checks on, metrics off.

With `sslRequired: all`, the gateway must reach Keycloak through its public https URL. That is the issuer in `gateway-oidc.env`. The appliance must therefore resolve its own name to itself. Plain http to the realm is refused (checked: 403).

## Tested

**Tests** (`python3 -m pytest keycloak/realm/tests`, with a suveryn-core checkout next to this one):
- the overrides are applied;
- everything else equals the development realm;
- weakened development realms are refused;
- the public URL must be plain https;
- each build gets a new secret, written 0600;
- the built files can't be committed.

**Imported into Keycloak 26.8.0** on the dev pod as a temporary realm, read back through the admin API, then deleted (2026-10-11):
- every setting above arrived as intended, including the client's exact URLs, PKCE S256 and the admin-events expiry;
- http requests to the realm were refused.

**Read by Keycloak 26.8.0** (`kc.sh show-config`): `keycloak.conf.example` has no unknown or rejected settings. It has not been started in production mode, because the dev pod has no TLS proxy. Do that on the appliance image.
